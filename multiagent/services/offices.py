# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Installed offices of level 1 (OFI-T03): data only, installed from a folder or a ``.zip``.

An office is an ``office.yaml`` with its areas (``multiagent.offices.schema``). Installing registers every area as an
installed package named ``<office>__<area>`` (``services.installed``, ``source: "office"``) in one all-or-nothing
change, proposes the team of each area (never approves it) and records the office in
``<data_dir>/installed/offices.json`` (``maf.installed-offices/1``). The ``office.yaml`` of each version is kept in
``installed/.offices/<id>/v<n>/``. Removing an office or an area keeps the copies and the run data. An office that
brings code (a file outside ``CODE_FREE_SUFFIXES``) is refused before anything of it is imported or run. Without
offices nothing is created and nothing new is read. Nothing here returns an absolute path.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

import yaml

from ..bundles import load_bundle
from ..config import IN_CHECKOUT, PROJECT_ROOT
from ..db.database import utcnow
from ..external_output import canonical_json, sha256
from ..offices.export import build_export, check_manifest
from ..offices.plan import is_office_group, office_group_id
from ..offices.requirements import (executor_capabilities, mock_capabilities, model_capabilities,
                                    office_requirements)
from ..offices.scene import SCENE_RUNS_MAX, SCENE_WINDOW_HOURS, build_scene
from ..offices.schema import (OFFICE_ID_PATTERN, OFFICE_SCHEMA, OfficeDefinition, OfficeDefinitionError,
                              area_bundle_name, load_office)
from ..tools.files import _is_link_or_junction
from ..teams.digest import team_sha256
from .bundle_scans import BundleScanServices
from .errors import (INVALID_DEFINITION, INVALID_REQUEST, INVALID_STATE, NOT_FOUND as SERVICE_NOT_FOUND, ServiceError,
                     service_errors, translate)
from .installed import (CODE_FREE_SUFFIXES, SOURCE_OFFICE, STATE_MISSING, STATE_OK, ZIP_EXTRACT_MAX_BYTES,
                        ZIP_MAX_FILES, ZIP_UPLOAD_MAX_BYTES, _conflict, _invalid, extract_zip_safely)
from .installed import package_level

if TYPE_CHECKING:
    from . import ApplicationServices

OFFICES_FILE = "offices.json"
OFFICES_SCHEMA = "maf.installed-offices/1"
SHIPPED_OFFICES = {"cafeteria": "examples/offices/cafeteria"}
# Proposed limits (design §13): the builder fixes them.
OFFICE_FILE_MAX_BYTES = 2**20
OFFICE_MAX_BYTES = ZIP_EXTRACT_MAX_BYTES
OFFICE_MAX_FILES = ZIP_MAX_FILES
OFFICE_MAX_AREAS = 20
OFFICE_CODE_FILES_SHOWN = 20
CODE_FIX_TEXT = "Esta oficina trae código: impórtala desde la ficha de la GUI en modo avanzado"

_OFFICES_DIR = ".offices"
_STAGING = ".staging"
_ID = re.compile(OFFICE_ID_PATTERN + r"\Z")
_EVENT_IDS = 10      # ids listed in an event: the payload of an event is small


def _too_large(message: str, **details: Any) -> ServiceError:
    return ServiceError(INVALID_REQUEST, message, status=422, details={"reason": "office_too_large", **details})


def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_none(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value


def _warning(code: str, location: str, message: str) -> dict[str, str]:
    return {"code": code, "location": location, "message": message}


def _scrub(value: Any, roots: list[str]) -> Any:
    """The text of ``value`` without the folders of the preparation: an answer never names a path of this machine."""
    if isinstance(value, str):
        for root in roots:
            value = value.replace(root, "<office>")
        return value
    if isinstance(value, dict):
        return {key: _scrub(item, roots) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub(item, roots) for item in value]
    return value


def _record_of(office: OfficeDefinition) -> dict[str, Any]:
    """What the registry keeps of an ``office.yaml``: everything but the warnings, as plain JSON."""
    return {
        "id": office.id, "name": office.name, "description": office.description, "local_only": office.local_only,
        "orchestrator": office.orchestrator.model_dump(mode="json"),
        "areas": [{"id": area.id, "name": area.name, "path": area.path, "team_sha256": area.team_sha256,
                   "sensitivity": area.sensitivity, "local_only": area.local_only, "requires": sorted(area.requires)}
                  for area in office.areas],
        "relations": [relation.model_dump(mode="json", by_alias=True) for relation in office.relations],
    }


def _office_of_record(record: dict[str, Any]) -> OfficeDefinition:
    """The validated definition of a record (the semantic checks run: cycles, unknown areas, warnings)."""
    return OfficeDefinition.model_validate({"schema": OFFICE_SCHEMA, **_drop_none(record)})


def _yaml_of_record(record: dict[str, Any]) -> bytes:
    document = {"schema": OFFICE_SCHEMA, **_drop_none(record)}
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True).encode("utf-8")


# --- sensitivity of the areas (SEN-T01): level -> data class, and the fixed note of «Privacidad» ----------------------

LEVELS = ("normal", "sensitive")
DATA_CLASS_OF = {"normal": "non_sensitive", "sensitive": "sensitive"}
_LEVEL_RANK = {None: 0, "normal": 1, "sensitive": 2}     # an area without a level is the lowest (Decision 15)
LOCAL_ONLY_LABEL = "local_only"
PRIVACY_APPROVAL_KIND = "office_privacy_change"
PRIVACY_NOTE: tuple[str, ...] = (
    "Contraseñas, claves y tokens: siempre te pregunto, uno por uno.",
    "Las áreas marcadas «Solo local».",
    "Con un ejecutor (Claude, Codex, Copilot, agy), el agente puede leer toda la carpeta de trabajo de la oficina, "
    "no solo la tarea.",
)


def _level_label(sensitivity: str | None, local_only: bool) -> str:
    """``unset``, ``normal`` or ``sensitive``, plus ``+local_only``: the short text of one level in an approval or an event."""
    return f"{sensitivity or 'unset'}{'+' + LOCAL_ONLY_LABEL if local_only else ''}"


def _parse_label(label: Any) -> tuple[str | None, bool] | None:
    if not isinstance(label, str):
        return None
    base, _, flag = label.partition("+")
    if base not in ("unset", *LEVELS) or flag not in ("", LOCAL_ONLY_LABEL):
        return None
    return (None if base == "unset" else base), flag == LOCAL_ONLY_LABEL


def _privacy_digest(office_id: str, area_id: str | None, before: str, after: str) -> str:
    return sha256(canonical_json({"office": office_id, "area": area_id, "from": before, "to": after}))


def _cloud_permission_digest(office_id: str, area_id: str | None, scope: str) -> str:
    return sha256(canonical_json({"office": office_id, "area": area_id, "scope": scope, "from": False, "to": True}))


OFFICE_NOTICE = ("Las instrucciones de esta oficina las escribió otra persona. Un ejecutor activado las sigue dentro "
                 "de su carpeta de trabajo")
STAGE_TTL_SECONDS = 3600
_STAGE_ID = re.compile(r"[0-9a-f]{32}\Z")


_STEP_AGENTS_CACHE: dict[tuple[str, int, int], dict[str, str]] = {}


def _workflow_step_agents(path: Path) -> dict[str, str]:
    """``{step_id: agent}`` of the steps of one workflow file that name an agent and no role; ``{}`` if unreadable.

    Read-only and tolerant: the scene never validates a workflow. The result is cached by path, size and mtime."""
    try:
        info = path.stat()
        key = (str(path), info.st_size, info.st_mtime_ns)
        if key in _STEP_AGENTS_CACHE:
            return _STEP_AGENTS_CACHE[key]
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        found: dict[str, str] = {}
        for step in document.get("steps") or []:
            if (isinstance(step, dict) and isinstance(step.get("id"), str) and not step.get("role")
                    and isinstance(step.get("agent"), str) and step["agent"]):
                found[step["id"]] = step["agent"]
    except Exception:       # a missing, unreadable or odd file leaves the step with the area
        return {}
    if len(_STEP_AGENTS_CACHE) > 512:
        _STEP_AGENTS_CACHE.clear()
    _STEP_AGENTS_CACHE[key] = found
    return found


def _area_step_agents(folder: Path) -> dict[str, dict[str, str]]:
    """``{workflow_id: {step_id: agent}}`` read from ``<folder>/workflows/*.yaml`` without loading the package."""
    result: dict[str, dict[str, str]] = {}
    try:
        files = sorted((folder / "workflows").glob("*.yaml"))
    except OSError:
        return result
    for file in files:
        agents = _workflow_step_agents(file)
        if agents:
            result[file.stem] = agents
    return result


class OfficeServices:
    """The registry of installed offices of one facade. Entries live in ``<data_dir>/installed/offices.json``."""

    def __init__(self, services: ApplicationServices):
        self._services = services
        self._lock = threading.RLock()

    # --- registry file ------------------------------------------------------

    @property
    def _root(self) -> Path:
        return self._services.settings.data_dir / "installed"

    @property
    def _registry_path(self) -> Path:
        return self._root / OFFICES_FILE

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            data = json.loads(self._registry_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise ValueError(f"The installed offices registry ({OFFICES_FILE}) cannot be read") from None
        entries = data.get("offices") if isinstance(data, dict) and data.get("schema") == OFFICES_SCHEMA else None
        if not isinstance(entries, dict):
            raise ValueError(f"The installed offices registry ({OFFICES_FILE}) has an unknown format")
        return {key: entry for key, entry in entries.items() if isinstance(entry, dict)}

    def _write(self, entries: dict[str, dict[str, Any]]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        temporary = self._registry_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"schema": OFFICES_SCHEMA, "offices": entries},
                                        ensure_ascii=False, sort_keys=True, indent=2), encoding="utf-8")
        temporary.replace(self._registry_path)

    def load_registry(self) -> None:
        """Read ``offices.json`` at the start; a hand-edited or damaged file stops the start (``ValueError``).

        It reads nothing else: the areas are packages and ``services.installed`` loads them. An office whose area is
        missing or not ``ok`` is ``degraded`` when it is listed. Without offices there is no file and nothing happens.
        """
        if getattr(self._services.system, "settings", None) is None:
            return  # a facade over a stand-in system (some unit tests) has no data_dir to read
        with self._lock:
            for key, entry in self._read().items():
                definition = entry.get("definition")
                areas = definition.get("areas") if isinstance(definition, dict) else None
                if (not _ID.fullmatch(str(key)) or not isinstance(definition, dict) or definition.get("id") != key
                        or not isinstance(areas, list) or not all(
                            isinstance(area, dict) and _ID.fullmatch(str(area.get("id"))) for area in areas)):
                    raise ValueError(f"The installed offices registry ({OFFICES_FILE}) has an invalid entry: {key!r}")

    # --- reading ------------------------------------------------------------

    def _team_state(self, bundle: str) -> dict[str, Any]:
        """The approved version of the team of a package, or else its latest one: ``{status, version_id}``."""
        try:
            store = self._services.teams.store
            row = store.latest_approved(bundle)
            if row is None:
                rows = store.list(bundle)
                row = rows[-1] if rows else None
        except Exception:  # a facade without a team database: the area simply has no team yet
            row = None
        return {"status": row["status"] if row else None, "version_id": row["id"] if row else None}

    def _public(self, entry: dict[str, Any], registered: dict[str, dict[str, Any]]) -> dict[str, Any]:
        definition = entry["definition"]
        office_id = definition["id"]
        areas, issues = [], []
        for area in definition["areas"]:
            bundle = area_bundle_name(office_id, area["id"])
            package = registered.get(bundle)
            if package is None or package.get("source") != SOURCE_OFFICE or package.get("office") != office_id:
                package_state = STATE_MISSING
            else:
                package_state = self._services.installed._states.get(bundle, STATE_OK)
            if package_state != STATE_OK:
                issues.append({"code": f"area_{package_state}", "area": area["id"],
                               "message": f"The package of the area {area['id']} is {package_state}; reimport the office"})
            level = self._own_level(entry, area["id"])      # SEN-T01: what applies, not only what the office.yaml declares
            areas.append({"id": area["id"], "name": area["name"], "bundle": bundle, "sensitivity": level["sensitivity"],
                          "local_only": level["local_only"], "requires": list(area.get("requires") or []),
                          "package_state": package_state, "team": self._team_state(bundle)})
        return {"id": office_id, "name": definition["name"], "description": definition.get("description"),
                "version": entry.get("version"), "fingerprint": entry.get("fingerprint"),
                "installed_at": entry.get("installed_at"), "installed_by": entry.get("installed_by"),
                "channel": entry.get("channel"), "level": "data", "local_only": self._office_local(entry)[0],
                "state": "degraded" if issues else "ok", "issues": issues,
                "warnings": list(entry.get("warnings") or []), "areas": areas,
                "orchestrator": definition.get("orchestrator"), "relations": list(definition.get("relations") or [])}

    @service_errors
    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            entries = self._read()
            if not entries:
                return []
            registered = self._services.installed._read()
            return [self._public(entry, registered) for _, entry in sorted(entries.items())]

    @service_errors
    def get(self, office_id: str) -> dict[str, Any]:
        office_id = self._check_id(office_id, "office_id")
        with self._lock:
            entry = self._read().get(office_id)
            if entry is None:
                raise self._not_installed(office_id)
            return self._public(entry, self._services.installed._read())

    def office_of(self, bundle: str) -> tuple[str, str] | None:
        """``(office id, area id)`` of an installed package that is an area of an office, or ``None``."""
        try:
            entries = self._read()
        except ValueError:
            return None
        for office_id, entry in entries.items():
            for area in entry["definition"]["areas"]:
                if area_bundle_name(office_id, area["id"]) == bundle:
                    return office_id, area["id"]
        return None

    @staticmethod
    def _check_id(value: Any, field: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise _invalid(f"Invalid {field.replace('_', ' ')}", field=field)
        return value

    @staticmethod
    def _not_installed(office_id: str) -> ServiceError:
        return _conflict(f"Office not installed: {office_id}", "office_not_installed", office=office_id)

    # --- the preparation ----------------------------------------------------

    def _check_limits(self, root: Path) -> None:
        """``office_too_large`` for a file, a size or a count over the limits. Reads only the sizes of the files."""
        files = total = 0
        for current, directories, names in os.walk(root, followlinks=False):
            directories[:] = [name for name in directories if name != "__pycache__"]
            for name in names:
                path = Path(current) / name
                try:
                    size = path.lstat().st_size
                except OSError:
                    continue
                files, total = files + 1, total + size
                relative = path.relative_to(root).as_posix()
                if size > OFFICE_FILE_MAX_BYTES:
                    raise _too_large(f"A file of the office is larger than {OFFICE_FILE_MAX_BYTES} bytes",
                                     field="file", limit=OFFICE_FILE_MAX_BYTES, path=relative[:200])
                if files > OFFICE_MAX_FILES:
                    raise _too_large(f"The office has more than {OFFICE_MAX_FILES} files", field="files",
                                     limit=OFFICE_MAX_FILES)
                if total > OFFICE_MAX_BYTES:
                    raise _too_large(f"The office is larger than {OFFICE_MAX_BYTES} bytes", field="bytes",
                                     limit=OFFICE_MAX_BYTES)

    @contextmanager
    def _staged(self, *, folder: Any = None, data: Any = None) -> Iterator[Path]:
        """The office copied (a folder) or extracted (a ``.zip``) under ``installed/.staging/<uuid>/``; removed after.

        Errors leave without the path of the preparation, translated to ``ServiceError``.
        """
        installed_root = self._root
        staging = installed_root / _STAGING / uuid.uuid4().hex
        roots = [str(staging), str(staging.resolve()), staging.as_posix(), staging.resolve().as_posix()]
        try:
            try:
                if data is not None:
                    if not isinstance(data, (bytes, bytearray)):
                        raise _invalid("The .zip must be bytes", field="zip")
                    staging.parent.mkdir(parents=True, exist_ok=True)
                    root = extract_zip_safely(bytes(data), staging, max_bytes=OFFICE_MAX_BYTES, max_files=OFFICE_MAX_FILES)
                    check_manifest(root)
                    self._check_limits(root)
                else:
                    source = Path(folder).expanduser() if isinstance(folder, (str, Path)) else None
                    if source is None or not source.is_dir():
                        raise _invalid("The office folder does not exist", field="folder")
                    source = source.resolve()
                    if source.is_relative_to(installed_root.resolve()):
                        raise _invalid("The office folder is already inside the installed packages", field="folder")
                    self._check_limits(source)
                    copied = self._services.installed._copy_to_staging(source)
                    staging = copied
                    roots += [str(copied), str(copied.resolve()), copied.as_posix(), copied.resolve().as_posix()]
                    root = copied
                yield root
            except Exception as exc:
                error = translate(exc)
                if error is None:
                    raise
                error.message = _scrub(error.message, roots)
                error.details = _scrub(error.details, roots)
                if error is exc:
                    raise
                raise error from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def _office_error(exc: OfficeDefinitionError) -> ServiceError:
        issues = [{"code": item["code"], "location": item["location"], "message": item["message"]} for item in exc.issues]
        count = len(issues)
        return ServiceError(INVALID_DEFINITION, f"Invalid office definition: {count} problem{'' if count == 1 else 's'}",
                            status=422, details={"reason": issues[0]["code"] if issues else "office_invalid",
                                                 "issues": issues})

    def _read_office(self, root: Path) -> OfficeDefinition:
        try:
            office = load_office(root)
        except OfficeDefinitionError as exc:
            raise self._office_error(exc) from None
        if len(office.areas) > OFFICE_MAX_AREAS:
            raise _too_large(f"The office has more than {OFFICE_MAX_AREAS} areas", field="areas", limit=OFFICE_MAX_AREAS)
        self._refuse_code(root)
        return office

    @staticmethod
    def _refuse_code(root: Path) -> None:
        """Level 1 is data only: ``office_has_code`` lists the files (``OFFICE_CODE_FILES_SHOWN`` at most) and stops."""
        found: list[str] = []
        for current, directories, names in os.walk(root, followlinks=False):
            directories[:] = [name for name in directories if name != "__pycache__"]
            for name in names:
                if Path(name).suffix.lower() not in CODE_FREE_SUFFIXES:
                    found.append((Path(current) / name).relative_to(root).as_posix())
        if found:
            found.sort()
            raise _conflict("The office has code; level 1 offices have data only", "office_has_code",
                            files=found[:OFFICE_CODE_FILES_SHOWN], count=len(found), fix=CODE_FIX_TEXT)

    def _check_areas(self, root: Path, office: OfficeDefinition, areas: list[Any] | None = None) -> dict[str, dict[str, Any]]:
        """Validate each area as a package and its team digest; ``{area id: {folder, fingerprint, team_digest, has_team}}``."""
        settings = self._services.settings
        infos: dict[str, dict[str, Any]] = {}
        for area in areas if areas is not None else office.areas:
            folder = root.joinpath(*area.path.split("/"))
            validation = self._services.installed._validate(folder)
            team = load_bundle(folder, settings).team
            digest = team_sha256(team) if team is not None else None
            if area.team_sha256 is not None and area.team_sha256 != digest:
                raise _conflict(f"The team of the area {area.id} is not the one the office declares",
                                "office_team_mismatch", area=area.id)
            infos[area.id] = {"folder": folder, "fingerprint": BundleScanServices.fingerprint(folder),
                              "team_digest": digest, "has_team": team is not None, "team": team,
                              "validation": validation}
        return infos

    def _taken(self, bundle: str, registered: dict[str, dict[str, Any]]) -> bool:
        return bundle in registered or bundle in self._services._bundles

    # --- teams, version copies and events -----------------------------------

    def _propose(self, bundle: str, digest: str | None, *, actor: str, channel: str,
                 warnings: list[dict[str, str]], validated: dict[str, Any] | None = None
                 ) -> dict[str, Any] | None:
        """Propose the team of a package (never approve it). ``no_changes`` is not an error: the approved one stays."""
        if digest is None:
            return None
        teams = self._services.teams
        rows = teams.store.list(bundle)
        if rows and rows[-1]["sha256"] == digest and rows[-1]["status"] in ("proposed", "approved"):
            return {"bundle": bundle, "version_id": rows[-1]["id"], "status": rows[-1]["status"]}
        try:
            if validated is None:
                version = teams.propose(bundle, actor=actor, channel=channel)
            else:
                version = teams._propose_validated(bundle, validated["team"], validated["validation"],
                                                   actor=actor, channel=channel)
        except ServiceError as exc:
            if exc.details.get("reason") == "no_changes":
                return {"bundle": bundle, "version_id": exc.details.get("approved_version_id"), "status": "approved"}
            raise
        for item in version.get("warnings") or []:
            if isinstance(item, dict):
                warnings.append(_warning(str(item.get("code", "team_warning")), f"{bundle}:{item.get('location', '')}",
                                         str(item.get("message", ""))))
        return {"bundle": bundle, "version_id": version["id"], "status": version["status"]}

    def _next_version(self, office_id: str, offices: dict[str, dict[str, Any]]) -> int:
        numbers = [int(offices.get(office_id, {}).get("version") or 0)]
        base = self._root / _OFFICES_DIR / office_id
        if base.is_dir():
            numbers += [int(p.name[1:]) for p in base.iterdir() if p.name[:1] == "v" and p.name[1:].isdigit()]
        return max(numbers) + 1

    def _save_copy(self, office_id: str, version: int, content: bytes) -> Path:
        folder = self._root / _OFFICES_DIR / office_id / f"v{version}"
        folder.mkdir(parents=True, exist_ok=False)
        (folder / "office.yaml").write_bytes(content)
        return folder

    def _event(self, event_type: str, office_id: str, *, actor: str, channel: str, **payload: Any) -> None:
        payload = {key: (value[:_EVENT_IDS] if isinstance(value, list) else value) for key, value in payload.items()}
        store = self._services.tasks.store
        with store.transaction() as conn:
            store.append_event(conn, event_type, actor=actor, ref=office_id, cause=f"office:{office_id}",
                               payload={"office": office_id, "channel": channel, **payload})

    def _entry(self, office: OfficeDefinition, record: dict[str, Any], *, version: int, actor: str, channel: str,
               registered: dict[str, dict[str, Any]]) -> dict[str, Any]:
        areas = {area_bundle_name(record["id"], area["id"]):
                 registered.get(area_bundle_name(record["id"], area["id"]), {}).get("fingerprint")
                 for area in record["areas"]}
        fingerprint = sha256(canonical_json({"definition": record, "areas": areas}))
        return {"version": version, "fingerprint": fingerprint, "installed_at": utcnow(), "installed_by": actor,
                "channel": channel, "level": "data", "path": f"{_OFFICES_DIR}/{record['id']}/v{version}",
                "warnings": list(office.warnings), "definition": record}

    def _rollback(self, office_id: str, bundles: list[str], *, actor: str, channel: str) -> None:
        """Take the areas of a failed install out of the registry again; the original error is the one that counts."""
        try:
            self._services.installed.apply_batch(
                [{"op": "remove", "name": bundle} for bundle in bundles], actor=actor, channel=channel,
                source=SOURCE_OFFICE, office=office_id)
        except Exception:
            pass

    # --- import -------------------------------------------------------------

    @service_errors
    def import_folder(self, folder: Path, *, actor: str, channel: str = "python", into: str | None = None) -> dict[str, Any]:
        """Install the office in ``folder``, or with ``into`` add the single area it holds to that installed office."""
        from .projects import _user_actor

        actor = _user_actor(actor)
        into = self._check_id(into, "into") if into is not None else None
        with self._staged(folder=folder) as root:
            return self._import(root, actor=actor, channel=channel, into=into, source="")

    @service_errors
    def import_zip(self, data: bytes, *, actor: str, channel: str = "python", filename: str = "",
                   into: str | None = None) -> dict[str, Any]:
        from .projects import _user_actor

        actor = _user_actor(actor)
        into = self._check_id(into, "into") if into is not None else None
        with self._staged(data=data) as root:
            return self._import(root, actor=actor, channel=channel, into=into, source=str(filename or "")[:80])

    def _import(self, root: Path, *, actor: str, channel: str, into: str | None, source: str) -> dict[str, Any]:
        office = self._read_office(root)
        content = (root / "office.yaml").read_bytes()
        with self._lock:
            return (self._add_area(root, office, into, actor=actor, channel=channel, source=source) if into is not None
                    else self._install(root, office, content, actor=actor, channel=channel, source=source))

    def _install(self, root: Path, office: OfficeDefinition, content: bytes, *, actor: str, channel: str,
                 source: str) -> dict[str, Any]:
        offices = self._read()
        if office.id in offices:
            raise _conflict(f"Office already installed: {office.id}", "office_exists", office=office.id)
        installed = self._services.installed
        registered = installed._read()
        bundles = {area.id: area_bundle_name(office.id, area.id) for area in office.areas}
        for bundle in bundles.values():
            if self._taken(bundle, registered):
                raise _conflict(f"Bundle name in use: {bundle}", "bundle_name_in_use", bundle=bundle)
        infos = self._check_areas(root, office)
        installed.apply_batch([{"op": "install", "name": bundles[area.id], "folder": str(infos[area.id]["folder"])}
                               for area in office.areas],
                              actor=actor, channel=channel, source=SOURCE_OFFICE, office=office.id,
                              expected_fingerprints={bundles[area.id]: infos[area.id]["fingerprint"]
                                                     for area in office.areas})
        copy: Path | None = None
        try:
            warnings = list(office.warnings)
            teams = [state for area in office.areas for state in [self._propose(
                bundles[area.id], infos[area.id]["team_digest"], actor=actor, channel=channel, warnings=warnings,
                validated=infos[area.id])] if state]
            version = self._next_version(office.id, offices)
            copy = self._save_copy(office.id, version, content)
            entry = self._entry(office, _record_of(office), version=version, actor=actor, channel=channel,
                                registered=installed._read())
            offices[office.id] = entry
            self._write(offices)
            self._event("office.installed", office.id, actor=actor, channel=channel, version=version,
                        areas=[area.id for area in office.areas], source=source)
        except BaseException:
            self._rollback(office.id, list(bundles.values()), actor=actor, channel=channel)
            if copy is not None:
                shutil.rmtree(copy, ignore_errors=True)
            raise
        return {"office": self._public(entry, installed._read()), "teams": teams, "warnings": warnings,
                "changes": {"added": [area.id for area in office.areas], "changed": [], "removed": [], "unchanged": []}}

    def _add_area(self, root: Path, office: OfficeDefinition, into: str, *, actor: str, channel: str,
                  source: str) -> dict[str, Any]:
        """A folder or ``.zip`` with an ``office.yaml`` of one area (what «Export area» writes) joins an installed office."""
        offices = self._read()
        target = offices.get(into)
        if target is None:
            raise self._not_installed(into)
        if len(office.areas) != 1:
            raise ServiceError(INVALID_REQUEST, "To add to an office, the office.yaml must hold exactly one area",
                               status=422, details={"reason": "office_single_area_required", "areas": len(office.areas)})
        area = office.areas[0]
        record = json.loads(json.dumps(target["definition"]))
        if any(item["id"] == area.id for item in record["areas"]):
            raise _conflict(f"The office {into} already has the area {area.id}", "office_area_exists",
                            office=into, area=area.id)
        if len(record["areas"]) + 1 > OFFICE_MAX_AREAS:
            raise _too_large(f"The office would have more than {OFFICE_MAX_AREAS} areas", field="areas",
                             limit=OFFICE_MAX_AREAS)
        installed = self._services.installed
        bundle = area_bundle_name(into, area.id)
        if self._taken(bundle, installed._read()):
            raise _conflict(f"Bundle name in use: {bundle}", "bundle_name_in_use", bundle=bundle)
        infos = self._check_areas(root, office)
        added = _record_of(office)["areas"][0]
        known = {item["id"] for item in record["areas"]} | {area.id}
        record["areas"].append(added)
        extra: list[dict[str, str]] = []
        for relation in _record_of(office)["relations"]:   # a relation to an area this office does not have is a warning
            if relation["from"] in known and relation["to"] in known:
                if relation not in record["relations"]:
                    record["relations"].append(relation)
            else:
                extra.append(_warning("office_relation_unknown_area", f"relations[{relation['from']}->{relation['to']}]",
                                      f"Relation references an area that {into} does not have; it was not added"))
        installed.apply_batch([{"op": "install", "name": bundle, "folder": str(infos[area.id]["folder"])}],
                              actor=actor, channel=channel, source=SOURCE_OFFICE, office=into,
                              expected_fingerprints={bundle: infos[area.id]["fingerprint"]})
        copy: Path | None = None
        try:
            warnings: list[dict[str, str]] = []
            team = self._propose(bundle, infos[area.id]["team_digest"], actor=actor, channel=channel,
                                 warnings=warnings, validated=infos[area.id])
            definition = _office_of_record(record)
            version = self._next_version(into, offices)
            copy = self._save_copy(into, version, _yaml_of_record(record))
            entry = self._entry(definition, record, version=version, actor=actor, channel=channel,
                                registered=installed._read())
            self._carry_privacy(entry, target)
            offices[into] = entry
            self._write(offices)
            self._event("office.reimported", into, actor=actor, channel=channel, version=version, added=[area.id],
                        changed=[], removed=[], source=source)
        except BaseException:
            self._rollback(into, [bundle], actor=actor, channel=channel)
            if copy is not None:
                shutil.rmtree(copy, ignore_errors=True)
            raise
        unchanged = [item["id"] for item in record["areas"] if item["id"] != area.id]
        return {"office": self._public(entry, installed._read()), "teams": [team] if team else [],
                "warnings": [*definition.warnings, *extra, *warnings],
                "changes": {"added": [area.id], "changed": [], "removed": [], "unchanged": unchanged}}

    # --- reimport -----------------------------------------------------------

    @service_errors
    def reimport_folder(self, office_id: str, folder: Path, *, actor: str, channel: str = "python") -> dict[str, Any]:
        from .projects import _user_actor

        actor = _user_actor(actor)
        office_id = self._check_id(office_id, "office_id")
        with self._staged(folder=folder) as root:
            return self._reimport(office_id, root, actor=actor, channel=channel, source="")

    @service_errors
    def reimport_zip(self, office_id: str, data: bytes, *, actor: str, channel: str = "python",
                     filename: str = "") -> dict[str, Any]:
        from .projects import _user_actor

        actor = _user_actor(actor)
        office_id = self._check_id(office_id, "office_id")
        with self._staged(data=data) as root:
            return self._reimport(office_id, root, actor=actor, channel=channel, source=str(filename or "")[:80])

    def _reimport(self, office_id: str, root: Path, *, actor: str, channel: str, source: str) -> dict[str, Any]:
        with self._lock:
            offices = self._read()
            previous = offices.get(office_id)
            if previous is None:
                raise self._not_installed(office_id)
            office = self._read_office(root)
            if office.id != office_id:
                raise _conflict(f"The office.yaml is the office {office.id}, not {office_id}", "office_id_mismatch",
                                office=office_id, found=office.id)
            installed = self._services.installed
            registered = installed._read()
            old_areas = {area["id"]: area for area in previous["definition"]["areas"]}
            infos = self._check_areas(root, office)
            added, changed, unchanged = [], [], []
            for area in office.areas:
                bundle = area_bundle_name(office_id, area.id)
                package = registered.get(bundle)
                if area.id not in old_areas:
                    if self._taken(bundle, registered):
                        raise _conflict(f"Bundle name in use: {bundle}", "bundle_name_in_use", bundle=bundle)
                    added.append(area.id)
                elif (package is None or package.get("fingerprint") != infos[area.id]["fingerprint"]
                      or installed._states.get(bundle, STATE_OK) != STATE_OK):
                    changed.append(area.id)
                else:
                    unchanged.append(area.id)
            kept = {area.id for area in office.areas}
            removed = [area_id for area_id in old_areas if area_id not in kept]
            batch = ([{"op": "install", "name": area_bundle_name(office_id, a), "folder": str(infos[a]["folder"])} for a in added]
                     + [{"op": "reinstall", "name": area_bundle_name(office_id, a), "folder": str(infos[a]["folder"])}
                        for a in changed]
                     + [{"op": "remove", "name": area_bundle_name(office_id, a)} for a in removed
                        if area_bundle_name(office_id, a) in registered])
            if batch:   # a run in progress of a changed or removed area stops the whole change (bundle_busy)
                installed.apply_batch(
                    batch, actor=actor, channel=channel, source=SOURCE_OFFICE, office=office_id,
                    expected_fingerprints={area_bundle_name(office_id, area_id): infos[area_id]["fingerprint"]
                                           for area_id in [*added, *changed]})
            copy: Path | None = None
            try:
                warnings = list(office.warnings)
                teams = []
                for area in office.areas:
                    bundle = area_bundle_name(office_id, area.id)
                    if area.id in unchanged:
                        state = self._team_state(bundle)
                        state = {"bundle": bundle, **state} if state["version_id"] else None
                    else:
                        state = self._propose(bundle, infos[area.id]["team_digest"], actor=actor, channel=channel,
                                              warnings=warnings, validated=infos[area.id])
                    if state:
                        teams.append(state)
                record = _record_of(office)
                entry = previous
                levels = self._highest_levels(previous, record)    # SEN-T01: a reimport never lowers a level
                if batch or record != previous["definition"] or levels != previous.get("privacy"):
                    version = self._next_version(office_id, offices)
                    copy = self._save_copy(office_id, version, (root / "office.yaml").read_bytes())
                    entry = self._entry(office, record, version=version, actor=actor, channel=channel,
                                        registered=installed._read())
                    if levels:
                        entry["privacy"] = levels
                    offices[office_id] = entry
                    self._write(offices)
                    self._event("office.reimported", office_id, actor=actor, channel=channel, version=version,
                                added=added, changed=changed, removed=removed, source=source)
            except BaseException:
                if copy is not None:
                    shutil.rmtree(copy, ignore_errors=True)
                raise
            return {"office": self._public(entry, installed._read()), "teams": teams, "warnings": warnings,
                    "changes": {"added": added, "changed": changed, "removed": removed, "unchanged": unchanged}}

    # --- remove -------------------------------------------------------------

    @service_errors
    def remove(self, office_id: str, *, actor: str, channel: str = "python") -> dict[str, Any]:
        """Take the office and its areas out of the registry and the process. Copies and run data stay."""
        from .projects import _user_actor

        actor = _user_actor(actor)
        office_id = self._check_id(office_id, "office_id")
        with self._lock:
            offices = self._read()
            entry = offices.get(office_id)
            if entry is None:
                raise self._not_installed(office_id)
            registered = self._services.installed._read()
            area_ids = [area["id"] for area in entry["definition"]["areas"]]
            names = [area_bundle_name(office_id, area_id) for area_id in area_ids]
            batch = [{"op": "remove", "name": name} for name in names
                     if registered.get(name, {}).get("source") == SOURCE_OFFICE and registered[name].get("office") == office_id]
            if batch:
                self._services.installed.apply_batch(batch, actor=actor, channel=channel, source=SOURCE_OFFICE,
                                                     office=office_id)
            del offices[office_id]
            self._write(offices)
            self._event("office.removed", office_id, actor=actor, channel=channel, areas=area_ids)
            return {"id": office_id, "removed": True, "kept_data": True, "areas": area_ids, "removed_by": actor,
                    "channel": channel}

    @service_errors
    def remove_area(self, office_id: str, area_id: str, *, actor: str, channel: str = "python") -> dict[str, Any]:
        """Take one area out. The plan items and the relations that name it leave the new copy, with a warning each."""
        from .projects import _user_actor

        actor = _user_actor(actor)
        office_id = self._check_id(office_id, "office_id")
        area_id = self._check_id(area_id, "area_id")
        with self._lock:
            offices = self._read()
            entry = offices.get(office_id)
            if entry is None:
                raise self._not_installed(office_id)
            record = json.loads(json.dumps(entry["definition"]))
            if not any(area["id"] == area_id for area in record["areas"]):
                raise _conflict(f"The office {office_id} has no area {area_id}", "office_area_not_installed",
                                office=office_id, area=area_id)
            if len(record["areas"]) == 1:
                raise _conflict(f"{area_id} is the only area of {office_id}; remove the office instead",
                                "office_last_area", office=office_id, area=area_id)
            installed = self._services.installed
            bundle = area_bundle_name(office_id, area_id)
            registered = installed._read()
            record["areas"] = [area for area in record["areas"] if area["id"] != area_id]
            warnings: list[dict[str, str]] = []
            plan = record["orchestrator"].get("default_plan") or []
            dropped = {item["id"] for item in plan if item["area"] == area_id}
            for item_id in sorted(dropped):
                warnings.append(_warning("office_plan_item_removed", f"orchestrator.default_plan[{item_id}]",
                                         f"The plan item {item_id!r} named the removed area {area_id!r}; it left the plan"))
            kept = []
            for item in plan:
                if item["id"] in dropped:
                    continue
                after = [dep for dep in item.get("after") or [] if dep not in dropped]
                if after != (item.get("after") or []):
                    warnings.append(_warning("office_plan_after_removed", f"orchestrator.default_plan[{item['id']}].after",
                                             f"The plan item {item['id']!r} no longer waits for a removed item"))
                kept.append({**item, "after": after})
            record["orchestrator"]["default_plan"] = kept
            relations = []
            for relation in record["relations"]:
                if area_id in (relation["from"], relation["to"]):
                    warnings.append(_warning("office_relation_removed", f"relations[{relation['from']}->{relation['to']}]",
                                             f"The relation named the removed area {area_id!r}; it was dropped"))
                else:
                    relations.append(relation)
            record["relations"] = relations
            definition = _office_of_record(record)
            if registered.get(bundle, {}).get("source") == SOURCE_OFFICE and registered[bundle].get("office") == office_id:
                installed.apply_batch([{"op": "remove", "name": bundle}], actor=actor, channel=channel,
                                      source=SOURCE_OFFICE, office=office_id)
            version = self._next_version(office_id, offices)
            copy = self._save_copy(office_id, version, _yaml_of_record(record))
            try:
                new = self._entry(definition, record, version=version, actor=actor, channel=channel,
                                  registered=installed._read())
                self._carry_privacy(new, entry, drop=area_id)
                offices[office_id] = new
                self._write(offices)
                self._event("office.reimported", office_id, actor=actor, channel=channel, version=version, added=[],
                            changed=[], removed=[area_id])
            except BaseException:
                shutil.rmtree(copy, ignore_errors=True)
                raise
            return {"office": self._public(new, installed._read()), "removed": True, "kept_data": True,
                    "area": area_id, "warnings": [*definition.warnings, *warnings], "removed_by": actor,
                    "channel": channel}

    # --- requirements by capability (OFI-T06) ---------------------------------

    def requirements_candidates(self, *, rows: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
        """Who could cover an area: catalog models of a connected provider, active executors and, in Mock, ``mock``.

        When rows are omitted, ``connections.list()`` checks connections (including CLI detection, quota files and
        Ollama). Callers can pass saved rows to avoid those checks. It also reads the local activation file. ``local``
        is explicit: ``True`` for ``mock`` and for a model of ``tier: local``, ``False`` for executors and cloud models.
        """
        services = self._services
        rows = rows if rows is not None else services.connections.list()["connections"]
        ready = {row["id"] for row in rows if row.get("kind") == "model" and row.get("available")}
        found: list[dict[str, Any]] = []
        for spec in services.system.catalog.models.values():
            if spec.provider in ready:
                found.append({"id": spec.id, "kind": "model", "label": spec.id, "capabilities": model_capabilities(spec),
                              "local": str(spec.tier).casefold() == "local"})
        for executor_id in services.executor_activations.active_ids():
            found.append({"id": executor_id, "kind": "executor", "label": executor_id,
                          "capabilities": executor_capabilities(), "local": False})
        if getattr(services.settings, "mock_mode", False):
            found.append({"id": "mock", "kind": "mock", "label": "Mock (ejemplo)", "capabilities": mock_capabilities(),
                          "local": True})
        return found

    def requirements_for(self, definition: OfficeDefinition) -> dict[str, Any]:
        """``{office, areas}`` for an office still being prepared (the card of OFI-T04); ``requires`` only informs."""
        return office_requirements(definition, self.requirements_candidates())

    @service_errors
    def requirements(self, office_id: str) -> dict[str, Any]:
        """What covers each area of an installed office, or what is missing. ``office_not_installed`` if it is not there."""
        office_id = self._check_id(office_id, "office_id")
        with self._lock:
            entry = self._read().get(office_id)
            if entry is None:
                raise self._not_installed(office_id)
            definition = _office_of_record(entry["definition"])
        return self.requirements_for(definition)

    # --- sensitivity of the areas (SEN-T01) ---------------------------------
    #
    # What the office.yaml declares (``sensitivity`` and ``local_only`` of each area, ``local_only`` of the office) is
    # the starting level. What a person sets lives beside it in the entry of the office, under ``privacy``. Cloud
    # permission is person-only and never imported.

    @staticmethod
    def _own_level(entry: dict[str, Any], area_id: str) -> dict[str, Any]:
        """``{sensitivity, local_only, set_by, set_at, source}`` of the area itself (not of the office as a whole)."""
        declared = next((area for area in entry["definition"]["areas"] if area["id"] == area_id), {})
        record = ((entry.get("privacy") or {}).get("areas") or {}).get(area_id)
        if isinstance(record, dict):
            return {"sensitivity": record.get("sensitivity", declared.get("sensitivity")),
                    "local_only": bool(record.get("local_only", declared.get("local_only"))),
                    "set_by": record.get("set_by", entry.get("installed_by")),
                    "set_at": record.get("set_at", entry.get("installed_at")),
                    "source": record.get("source", "office")}
        return {"sensitivity": declared.get("sensitivity"), "local_only": bool(declared.get("local_only")),
                "set_by": entry.get("installed_by"), "set_at": entry.get("installed_at"), "source": "office"}

    @staticmethod
    def _office_local(entry: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        """Whether the whole office is «Solo local», and who said so."""
        record = (entry.get("privacy") or {}).get("office")
        if isinstance(record, dict) and record.get("local_only"):
            return True, {"set_by": record.get("set_by"), "set_at": record.get("set_at"), "source": record.get("source", "person")}
        declared = bool(entry["definition"].get("local_only"))
        return declared, {"set_by": entry.get("installed_by"), "set_at": entry.get("installed_at"), "source": "office"}

    @staticmethod
    def _class_of(level: dict[str, Any], office_local: bool) -> str | None:
        """The data class of an area (design 8.6): «Solo local» is ``restricted``; no level is ``None``."""
        if level["local_only"] or office_local:
            return "restricted"
        return DATA_CLASS_OF.get(level["sensitivity"])

    def area_class(self, bundle: str) -> str | None:
        """``non_sensitive``, ``sensitive`` or ``restricted`` for an installed area; ``None`` for an area without a level
        and for a package that is not an area of an office."""
        located = self.office_of(bundle)
        if located is None:
            return None
        entry = self._read().get(located[0])
        if entry is None:
            return None
        return self._class_of(self._own_level(entry, located[1]), self._office_local(entry)[0])

    @staticmethod
    def _cloud_record(record: Any) -> dict[str, Any]:
        if not isinstance(record, dict) or "cloud_allowed" not in record:
            return {"allowed": False, "set_by": None, "set_at": None, "source": "default"}
        metadata = record.get("cloud") if isinstance(record.get("cloud"), dict) else {}
        return {"allowed": record.get("cloud_allowed") is True, "set_by": metadata.get("set_by"),
                "set_at": metadata.get("set_at"), "source": metadata.get("source", "person")}

    @classmethod
    def _effective_cloud_record(cls, entry: dict[str, Any], area_id: str) -> dict[str, Any]:
        privacy = entry.get("privacy") or {}
        area = (privacy.get("areas") or {}).get(area_id)
        if isinstance(area, dict) and "cloud_allowed" in area:
            return {**cls._cloud_record(area), "scope": "area"}
        office = privacy.get("office")
        if isinstance(office, dict) and "cloud_allowed" in office:
            return {**cls._cloud_record(office), "scope": "office"}
        return {**cls._cloud_record(None), "scope": "default"}

    def cloud_permission_details(self, bundle: str) -> dict[str, Any] | None:
        """Effective saved permission and its provenance for a bundle in an installed office."""
        located = self.office_of(bundle)
        if located is None:
            return None
        office_id, area_id = located
        entry = self._read().get(office_id)
        if entry is None:
            return None
        return {"office": office_id, "area": area_id, **self._effective_cloud_record(entry, area_id)}

    def cloud_permission_for(self, bundle: str, area: str | None = None) -> bool:
        """Whether the installed area or its office allows cloud use (an explicit area value wins)."""
        permission = self.cloud_permission_details(bundle)
        return bool(permission and (area is None or area == permission["area"]) and permission["allowed"])

    @service_errors
    def privacy(self, office_id: str) -> dict[str, Any]:
        """The levels and cloud permission of an office, area by area, with their provenance."""
        office_id = self._check_id(office_id, "office_id")
        with self._lock:
            entry = self._read().get(office_id)
            if entry is None:
                raise self._not_installed(office_id)
            office_local, _ = self._office_local(entry)
            office_cloud = self._cloud_record((entry.get("privacy") or {}).get("office"))
            areas = []
            for area in entry["definition"]["areas"]:
                level = self._own_level(entry, area["id"])
                local_only = level["local_only"] or office_local
                areas.append({"id": area["id"], "sensitivity": level["sensitivity"], "local_only": local_only,
                              "data_class": self._class_of(level, office_local), "set_by": level["set_by"],
                              "set_at": level["set_at"], "source": level["source"],
                              "cloud": self._cloud_record(((entry.get("privacy") or {}).get("areas") or {}).get(area["id"])),
                              "cloud_effective": self._effective_cloud_record(entry, area["id"])["allowed"]})
            return {"office": office_id, "local_only": office_local, "cloud": office_cloud,
                    "areas": areas, "note": PRIVACY_NOTE}

    def _check_levels(self, office_id: str, area_id: str | None, sensitivity: Any, local_only: Any,
                      cloud_allowed: Any = None) -> dict[str, Any]:
        """The entry of the office, after validating the request; the area must exist and one level must be named."""
        office_id = self._check_id(office_id, "office_id")
        area_id = self._check_id(area_id, "area") if area_id is not None else None
        if sensitivity is not None and sensitivity not in LEVELS:
            raise _invalid("The sensitivity must be normal or sensitive", field="sensitivity", allowed=list(LEVELS))
        if local_only is not None and not isinstance(local_only, bool):
            raise _invalid("local_only must be true or false", field="local_only")
        if cloud_allowed is not None and cloud_allowed is not False:
            raise _invalid("Granting cloud use needs the one-use approval in «Privacidad»",
                           reason="office_cloud_grant_needs_gui", field="cloud_allowed")
        if sensitivity is None and local_only is None and cloud_allowed is None:
            raise _invalid("Name a sensitivity, local_only or cloud_allowed",
                           reason="office_privacy_nothing_to_change")
        if area_id is None and sensitivity is not None:
            raise _invalid("The sensitivity belongs to an area: name it; only local_only applies to the whole office",
                           reason="office_privacy_area_required", field="area")
        entry = self._read().get(office_id)
        if entry is None:
            raise self._not_installed(office_id)
        if area_id is not None and not any(area["id"] == area_id for area in entry["definition"]["areas"]):
            raise _conflict(f"The office {office_id} has no area {area_id}", "office_area_not_installed",
                            office=office_id, area=area_id)
        return entry

    def _current(self, entry: dict[str, Any], area_id: str | None) -> tuple[str | None, bool]:
        if area_id is None:
            return None, self._office_local(entry)[0]
        level = self._own_level(entry, area_id)
        return level["sensitivity"], level["local_only"]

    @staticmethod
    def _target(current: tuple[str | None, bool], sensitivity: str | None, local_only: bool | None) -> tuple[str | None, bool]:
        return (current[0] if sensitivity is None else sensitivity, current[1] if local_only is None else local_only)

    @staticmethod
    def _lowers(before: tuple[str | None, bool], after: tuple[str | None, bool]) -> bool:
        return _LEVEL_RANK[after[0]] < _LEVEL_RANK[before[0]] or (before[1] and not after[1])

    def _store_level(self, offices: dict[str, dict[str, Any]], office_id: str, area_id: str | None,
                     after: tuple[str | None, bool], *, actor: str, channel: str, direction: str) -> None:
        """Write the level a person set, and its event ``office.privacy_changed`` (who, the area and both levels)."""
        entry = offices[office_id]
        before = self._current(entry, area_id)
        privacy = json.loads(json.dumps(entry.get("privacy") or {}))
        record = {"set_by": actor, "set_at": utcnow(), "source": "person"}
        if area_id is None:
            old = privacy.get("office") if isinstance(privacy.get("office"), dict) else {}
            privacy["office"] = {**old, "local_only": after[1], **record}
        else:
            old = privacy.setdefault("areas", {}).get(area_id)
            old = old if isinstance(old, dict) else {}
            privacy["areas"][area_id] = {**old, "sensitivity": after[0], "local_only": after[1], **record}
        entry["privacy"] = privacy
        self._write(offices)
        self._event("office.privacy_changed", office_id, actor=actor, channel=channel, area=area_id, direction=direction,
                    **{"from": _level_label(*before), "to": _level_label(*after)})

    def _privacy_answer(self, office_id: str, area_id: str | None, before: tuple[str | None, bool],
                        after: tuple[str | None, bool], changed: bool) -> dict[str, Any]:
        return {**self.privacy(office_id), "area": area_id, "from": _level_label(*before), "to": _level_label(*after),
                "changed": changed}

    @service_errors
    def raise_level(self, office_id: str, area_id: str | None, *, sensitivity: str | None = None,
                    local_only: bool | None = None, cloud_allowed: bool | None = None,
                    actor: str, channel: str = "python") -> dict[str, Any]:
        """Raise privacy levels or revoke cloud permission; granting cloud use needs the one-use approval."""
        from .projects import _user_actor

        actor = _user_actor(actor)
        with self._lock:
            entry = self._check_levels(office_id, area_id, sensitivity, local_only, cloud_allowed)
            before = self._current(entry, area_id)
            after = self._target(before, sensitivity, local_only)
            if self._lowers(before, after):
                raise _conflict("Lowering a level is done in «Privacidad», with the one-use code",
                                "office_level_lowering_needs_gui", office=office_id, area=area_id,
                                **{"from": _level_label(*before), "to": _level_label(*after)})
            level_changed = after != before
            cloud_changed = cloud_allowed is False and self._cloud_setting_needs_update(entry, area_id)
            if not level_changed and not cloud_changed:
                return self._privacy_answer(office_id, area_id, before, after, False)
            if level_changed:
                self._store_level(self._read(), office_id, area_id, after, actor=actor, channel=channel, direction="raise")
            if cloud_changed:
                self._store_cloud_permission(self._read(), office_id, area_id, False, actor=actor, channel=channel,
                                             direction="revoke")
            return self._privacy_answer(office_id, area_id, before, after, level_changed or cloud_changed)

    @staticmethod
    def _cloud_setting_needs_update(entry: dict[str, Any], area_id: str | None) -> bool:
        privacy = entry.get("privacy") or {}
        record = privacy.get("office") if area_id is None else (privacy.get("areas") or {}).get(area_id)
        if area_id is not None and (not isinstance(record, dict) or "cloud_allowed" not in record):
            return True
        return isinstance(record, dict) and record.get("cloud_allowed") is not False

    def _store_cloud_permission(self, offices: dict[str, dict[str, Any]], office_id: str, area_id: str | None,
                                allowed: bool, *, actor: str, channel: str, direction: str) -> dict[str, Any]:
        entry = offices[office_id]
        before = (self._effective_cloud_record(entry, area_id)["allowed"] if area_id is not None
                  else self._cloud_record((entry.get("privacy") or {}).get("office"))["allowed"])
        privacy = json.loads(json.dumps(entry.get("privacy") or {}))
        record = (privacy.setdefault("office", {}) if area_id is None
                  else privacy.setdefault("areas", {}).setdefault(area_id, {}))
        set_at = utcnow()
        record.update({"cloud_allowed": allowed, "cloud": {"set_by": actor, "set_at": set_at, "source": "person"}})
        entry["privacy"] = privacy
        self._write(offices)
        self._event("office.cloud_permission_changed", office_id, actor=actor, channel=channel, area=area_id,
                    scope="office" if area_id is None else "area", direction=direction,
                    **{"from": before, "to": allowed})
        return {"allowed": allowed, "set_by": actor, "set_at": set_at, "source": "person"}

    def _cloud_target(self, office_id: str, area_id: str | None) -> dict[str, Any]:
        office_id = self._check_id(office_id, "office_id")
        area_id = self._check_id(area_id, "area") if area_id is not None else None
        entry = self._read().get(office_id)
        if entry is None:
            raise self._not_installed(office_id)
        if area_id is not None and not any(area["id"] == area_id for area in entry["definition"]["areas"]):
            raise _conflict(f"The office {office_id} has no area {area_id}", "office_area_not_installed",
                            office=office_id, area=area_id)
        return entry

    @service_errors
    def request_cloud_permission(self, office_id: str, area_id: str | None = None) -> dict[str, Any]:
        """Request the hidden one-use approval to allow cloud use at the office or area scope."""
        office_id = self._check_id(office_id, "office_id")
        area_id = self._check_id(area_id, "area") if area_id is not None else None
        with self._lock:
            entry = self._cloud_target(office_id, area_id)
            privacy = entry.get("privacy") or {}
            record = privacy.get("office") if area_id is None else (privacy.get("areas") or {}).get(area_id)
            if isinstance(record, dict) and record.get("cloud_allowed") is True:
                raise _invalid("Cloud use is already allowed at this scope", reason="office_cloud_already_allowed",
                               office=office_id, area=area_id)
            scope = "office" if area_id is None else "area"
            digest = _cloud_permission_digest(office_id, area_id, scope)
            destination = {"office": office_id, "area": area_id or ""}
            approvals = self._services.user_approvals
            found = approvals.pending_like(PRIVACY_APPROVAL_KIND, sha256=digest, destination=destination)
            approval = found or approvals.request(
                kind=PRIVACY_APPROVAL_KIND, destination=destination, sha256=digest,
                location={"source": "office_cloud_permission", "scope": scope, "from": False, "to": True})
            return {"approval_id": approval["id"], "sha256": approval["sha256"]}

    @service_errors
    def grant_cloud_permission(self, office_id: str, area_id: str | None = None, *, approval_id: str,
                               actor: str = "user:operator", channel: str = "gui") -> dict[str, Any]:
        """Spend the approved one-use request and enable cloud use at the exact office or area scope."""
        from .projects import _user_actor

        actor = _user_actor(actor)
        office_id = self._check_id(office_id, "office_id")
        area_id = self._check_id(area_id, "area") if area_id is not None else None
        scope = "office" if area_id is None else "area"
        with self._lock:
            offices = self._read()
            entry = self._cloud_target(office_id, area_id)
            privacy = entry.get("privacy") or {}
            record = privacy.get("office") if area_id is None else (privacy.get("areas") or {}).get(area_id)
            item = self._services.user_approvals.peek(approval_id) if isinstance(approval_id, str) and approval_id else None
            location = item.get("location") if item else None
            destination = {"office": office_id, "area": area_id or ""}
            digest = _cloud_permission_digest(office_id, area_id, scope)
            invalid = (
                isinstance(record, dict) and record.get("cloud_allowed") is True
                or item is None or item.get("kind") != PRIVACY_APPROVAL_KIND or item.get("state") != "approved"
                or not isinstance(location, dict) or location.get("source") != "office_cloud_permission"
                or location.get("scope") != scope or location.get("from") is not False or location.get("to") is not True
                or item.get("destination") != destination or item.get("sha256") != digest
            )
            if invalid or not self._services.user_approvals.consume(
                    approval_id, sha256=digest, destination=destination):
                raise self._cloud_not_approved(office_id, area_id)
            saved = self._store_cloud_permission(offices, office_id, area_id, True, actor=actor, channel=channel,
                                                 direction="grant")
            return {**self.privacy(office_id), "area": area_id, "scope": scope, "changed": True,
                    "cloud_permission": saved}

    @service_errors
    def request_lowering(self, office_id: str, area_id: str | None, *, sensitivity: str | None = None,
                         local_only: bool | None = None) -> dict[str, Any]:
        """Ask for the one-use approval ``office_privacy_change`` (hidden) of a lower level; the person decides it
        in «Privacidad». The ``sha256`` binds the office, the area and both levels. Changes nothing yet."""
        with self._lock:
            entry = self._check_levels(office_id, area_id, sensitivity, local_only)
            before = self._current(entry, area_id)
            after = self._target(before, sensitivity, local_only)
            if not self._lowers(before, after):
                raise _invalid("That is not a lower level: raise it with raise_level", reason="office_privacy_not_lower",
                               office=office_id, area=area_id)
            digest = _privacy_digest(office_id, area_id, _level_label(*before), _level_label(*after))
            destination = {"office": office_id, "area": area_id or ""}
            approvals = self._services.user_approvals
            found = approvals.pending_like(PRIVACY_APPROVAL_KIND, sha256=digest, destination=destination)
            if found is not None:
                return found
            return approvals.request(
                kind=PRIVACY_APPROVAL_KIND, destination=destination, sha256=digest,
                location={"source": "office_privacy", "from": _level_label(*before), "to": _level_label(*after)})

    @service_errors
    def lower_level(self, office_id: str, area_id: str | None, *, approval_id: str, actor: str = "user:operator",
                    channel: str = "gui") -> dict[str, Any]:
        """Spend the approved ``office_privacy_change`` once and store the lower level. Without it, or when the
        level is no longer the one the person saw, ``office_privacy_change_not_approved``."""
        from .projects import _user_actor

        actor = _user_actor(actor)
        office_id = self._check_id(office_id, "office_id")
        area_id = self._check_id(area_id, "area") if area_id is not None else None
        with self._lock:
            offices = self._read()
            entry = offices.get(office_id)
            if entry is None:
                raise self._not_installed(office_id)
            approvals = self._services.user_approvals
            item = approvals.peek(approval_id) if isinstance(approval_id, str) and approval_id else None
            location = item.get("location") if item else None
            after = _parse_label(location.get("to")) if isinstance(location, dict) else None
            if (item is None or item.get("kind") != PRIVACY_APPROVAL_KIND or item.get("state") != "approved"
                    or after is None or (area_id is None and after[0] is not None)):
                raise self._not_approved(office_id, area_id)
            before = self._current(entry, area_id)
            digest = _privacy_digest(office_id, area_id, _level_label(*before), _level_label(*after))
            if not self._lowers(before, after) or not approvals.consume(
                    approval_id, sha256=digest, destination={"office": office_id, "area": area_id or ""}):
                raise self._not_approved(office_id, area_id)
            self._store_level(offices, office_id, area_id, after, actor=actor, channel=channel, direction="lower")
            return self._privacy_answer(office_id, area_id, before, after, True)

    @staticmethod
    def _not_approved(office_id: str, area_id: str | None) -> ServiceError:
        return ServiceError(INVALID_STATE, "Lowering a level needs the person's one-use approval", status=409,
                            details={"reason": "office_privacy_change_not_approved", "office": office_id, "area": area_id})

    @staticmethod
    def _cloud_not_approved(office_id: str, area_id: str | None) -> ServiceError:
        return ServiceError(INVALID_STATE, "Granting cloud use needs the person's one-use approval", status=409,
                            details={"reason": "office_privacy_change_not_approved", "office": office_id, "area": area_id})

    @staticmethod
    def _carry_privacy(entry: dict[str, Any], previous: dict[str, Any], *, drop: str | None = None) -> None:
        """Keep what the person set when an area joins or leaves: the new entry of the office reads the same levels."""
        privacy = json.loads(json.dumps(previous.get("privacy") or {}))
        if drop is not None and isinstance(privacy.get("areas"), dict):
            privacy["areas"].pop(drop, None)
        if privacy.get("areas") or privacy.get("office"):
            entry["privacy"] = {key: value for key, value in privacy.items() if value}

    def _highest_levels(self, previous: dict[str, Any], record: dict[str, Any]) -> dict[str, Any] | None:
        """The ``privacy`` of a reimported office: for each area it had, the highest of its level and the new
        declaration (``sensitive`` over ``normal`` over nothing; ``local_only`` is added, never removed). An area that
        is new takes what the office declares. ``None`` when the office.yaml already says it all."""
        areas: dict[str, Any] = {}
        for area in record["areas"]:
            if not any(old["id"] == area["id"] for old in previous["definition"]["areas"]):
                continue
            held = self._own_level(previous, area["id"])
            declared = (area.get("sensitivity"), bool(area.get("local_only")))
            sensitivity = held["sensitivity"] if _LEVEL_RANK[held["sensitivity"]] > _LEVEL_RANK[declared[0]] else declared[0]
            local_only = held["local_only"] or declared[1]
            if (sensitivity, local_only) != declared:
                areas[area["id"]] = {"sensitivity": sensitivity, "local_only": local_only, "set_by": held["set_by"],
                                     "set_at": held["set_at"], "source": held["source"]}
            old_cloud = (((previous.get("privacy") or {}).get("areas") or {}).get(area["id"]))
            if isinstance(old_cloud, dict) and "cloud_allowed" in old_cloud:
                areas.setdefault(area["id"], {}).update(
                    {key: old_cloud[key] for key in ("cloud_allowed", "cloud") if key in old_cloud})
        held_office, who = self._office_local(previous)
        privacy: dict[str, Any] = {}
        if areas:
            privacy["areas"] = areas
        if held_office and not record.get("local_only"):
            privacy["office"] = {"local_only": True, **who}
        old_office = (previous.get("privacy") or {}).get("office")
        if isinstance(old_office, dict) and "cloud_allowed" in old_office:
            privacy.setdefault("office", {}).update(
                {key: old_office[key] for key in ("cloud_allowed", "cloud") if key in old_office})
        return privacy or None

    # --- allow-listed export (OFI-T05) -----------------------------------------

    @service_errors
    def export(self, office_id: str, *, area: str | None = None, include_code: bool = False) -> dict[str, Any]:
        """Export the installed copies of an office, or one of its areas, as a checked .zip."""
        office_id = self._check_id(office_id, "office_id")
        area = self._check_id(area, "area") if area is not None else None
        if not isinstance(include_code, bool):
            raise _invalid("include_code must be a boolean", field="include_code")
        with self._lock:
            entry = self._read().get(office_id)
            if entry is None:
                raise self._not_installed(office_id)
            version = entry.get("version")
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise ServiceError(INVALID_REQUEST, "The installed office copy is unavailable", status=409,
                                   details={"reason": "office_copy_missing", "office": office_id})
            office_copy = self._root / _OFFICES_DIR / office_id / f"v{version}" / "office.yaml"
            try:
                if office_copy.is_symlink() or not office_copy.resolve().is_relative_to(self._root.resolve()):
                    raise ServiceError(INVALID_REQUEST, "The installed office copy is unavailable", status=409,
                                       details={"reason": "office_copy_missing", "office": office_id})
                raw = yaml.safe_load(office_copy.read_bytes())
                definition = OfficeDefinition.model_validate(raw)
            except (OSError, RuntimeError):
                raise ServiceError(INVALID_REQUEST, "The installed office copy is unavailable", status=409,
                                   details={"reason": "office_copy_missing", "office": office_id}) from None
            except (yaml.YAMLError, ValueError):
                raise ServiceError(INVALID_REQUEST, "The installed office definition cannot be exported", status=409,
                                   details={"reason": "office_copy_invalid", "office": office_id}) from None
            if definition.id != office_id:
                raise ServiceError(INVALID_REQUEST, "The installed office definition cannot be exported", status=409,
                                   details={"reason": "office_copy_invalid", "office": office_id})
            if area is not None and not any(item.id == area for item in definition.areas):
                raise ServiceError(INVALID_REQUEST, f"Area not found: {area}", status=422,
                                   details={"reason": "office_area_not_found", "area": area})

            registered = self._services.installed._read()
            selected = [item for item in definition.areas if area is None or item.id == area]
            files: dict[str, bytes] = {}
            installed_root = self._services.installed._root.resolve()
            for item in selected:
                bundle = area_bundle_name(office_id, item.id)
                package = registered.get(bundle)
                if (package is None or package.get("source") != SOURCE_OFFICE or package.get("office") != office_id
                        or self.office_of(bundle) != (office_id, item.id)):
                    raise ServiceError(INVALID_REQUEST, f"The installed area {item.id} is unavailable", status=409,
                                       details={"reason": "office_area_missing", "area": item.id})
                folder = self._services.installed._folder_of(package)
                if not folder.is_relative_to(installed_root) or not folder.is_dir():
                    raise ServiceError(INVALID_REQUEST, f"The installed area {item.id} is unavailable", status=409,
                                       details={"reason": "office_area_missing", "area": item.id})

                def walk_error(error: OSError) -> None:
                    raise error

                try:
                    for current, directories, names in os.walk(folder, followlinks=False, onerror=walk_error):
                        here = Path(current)
                        directories[:] = [name for name in directories if name != "__pycache__"]
                        for name in directories:
                            directory = here / name
                            try:
                                unsafe = directory.is_symlink() or not directory.resolve().is_relative_to(folder)
                            except (OSError, RuntimeError):
                                unsafe = True
                            if unsafe:
                                raise ServiceError(INVALID_REQUEST, "The installed area contains an unsafe path",
                                                   status=409, details={"reason": "office_area_unsafe", "area": item.id})
                        for name in names:
                            path = here / name
                            try:
                                unsafe = path.is_symlink() or not path.resolve().is_relative_to(folder)
                            except (OSError, RuntimeError):
                                unsafe = True
                            if unsafe:
                                raise ServiceError(INVALID_REQUEST, "The installed area contains an unsafe path",
                                                   status=409, details={"reason": "office_area_unsafe", "area": item.id})
                            relative = path.relative_to(folder).as_posix()
                            files[f"{item.path.rstrip('/')}/{relative}"] = path.read_bytes()
                except OSError:
                    raise ServiceError(INVALID_REQUEST, f"The installed area {item.id} cannot be read", status=409,
                                       details={"reason": "office_area_unavailable", "area": item.id}) from None

            archive, manifest = build_export(files, definition, include_code=include_code, area=area)
            if len(archive) > ZIP_UPLOAD_MAX_BYTES:
                raise _too_large(f"The office export is larger than {ZIP_UPLOAD_MAX_BYTES} bytes",
                                 field="zip", limit=ZIP_UPLOAD_MAX_BYTES)
        filename = f"{office_id}{'-' + area if area is not None else ''}.zip"
        return {"filename": filename, "data": archive, "manifest": manifest, "warnings": manifest["warnings"]}

    # --- office orchestrator (ESC-T02) ---------------------------------------

    @service_errors
    def plan_definition(self, office_id: str) -> OfficeDefinition:
        """Return the validated installed definition for internal plan drafting."""
        office_id = self._check_id(office_id, "office_id")
        with self._lock:
            entry = self._read().get(office_id)
            if entry is None:
                raise self._not_installed(office_id)
            return _office_of_record(entry["definition"])

    @service_errors
    def plan_draft(self, office_id: str, text: str, *, confirm_cloud: bool = False,
                   approval_id: str | None = None) -> dict[str, Any]:
        """Draft an office plan without saving, approving or starting it."""
        return self._services.new_session.draft_plan(
            text, confirm_cloud=confirm_cloud, approval_id=approval_id, office=office_id)

    # --- install card (OFI-T04) ----------------------------------------------

    clock = staticmethod(time.time)

    @staticmethod
    def _install_error(reason: str, message: str, **details: Any) -> ServiceError:
        return ServiceError(INVALID_STATE, message, status=409, details={"reason": reason, **details})

    def _stage_folder(self, stage_id: str) -> Path:
        if not isinstance(stage_id, str) or _STAGE_ID.fullmatch(stage_id) is None:
            raise self._install_error("office_stage_not_found", "Office preparation not found")
        return self._root / _STAGING / stage_id

    def _stage_record(self, stage_id: str) -> tuple[Path, dict[str, Any]]:
        stage = self._stage_folder(stage_id)
        metadata_path = stage / "stage.json"
        try:
            if _is_link_or_junction(stage) or _is_link_or_junction(metadata_path):
                raise OSError
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise self._install_error("office_stage_not_found", "Office preparation not found") from None
        if not isinstance(metadata, dict) or metadata.get("stage_id") != stage_id:
            raise self._install_error("office_stage_not_found", "Office preparation not found")
        if (metadata.get("kind") not in {"install", "reimport", "area"}
                or not isinstance(metadata.get("office_id"), str) or not _ID.fullmatch(metadata["office_id"])
                or not isinstance(metadata.get("fingerprint"), str)
                or re.fullmatch(r"[0-9a-f]{64}", metadata["fingerprint"]) is None
                or metadata.get("into") is not None and
                (not isinstance(metadata["into"], str) or not _ID.fullmatch(metadata["into"]))
                or metadata.get("reimport") is not None and
                (not isinstance(metadata["reimport"], str) or not _ID.fullmatch(metadata["reimport"]))):
            raise self._install_error("office_stage_not_found", "Office preparation not found")
        created_at = metadata.get("created_at")
        if isinstance(created_at, bool) or not isinstance(created_at, (int, float)):
            raise self._install_error("office_stage_not_found", "Office preparation not found")
        if self.clock() - created_at > STAGE_TTL_SECONDS:
            self._reject_stage_approval(metadata)
            shutil.rmtree(stage, ignore_errors=True)
            raise self._install_error("office_stage_expired", "Office preparation expired", stage_id=stage_id)
        content = stage / "source"
        if (_is_link_or_junction(content) or not content.is_dir()
                or not content.resolve().is_relative_to(stage.resolve())):
            raise self._install_error("office_stage_not_found", "Office preparation not found")
        for current, directories, files in os.walk(content, followlinks=False):
            if any(_is_link_or_junction(Path(current) / name) for name in (*directories, *files)):
                raise self._install_error("office_changed_since_card", "The office contains a linked file",
                                          stage_id=stage_id)
        return content, metadata

    def _reject_stage_approval(self, metadata: dict[str, Any]) -> None:
        approval_id = metadata.get("approval_id")
        if not isinstance(approval_id, str):
            return
        approval = self._services.user_approvals.peek(approval_id)
        if approval is not None and approval.get("state") == "pending":
            self._services.user_approvals.decide(approval_id, "reject", actor="system:office")

    def _definition_for_stage(self, root: Path) -> OfficeDefinition:
        try:
            definition = load_office(root)
        except OfficeDefinitionError as exc:
            raise self._office_error(exc) from None
        if len(definition.areas) > OFFICE_MAX_AREAS:
            raise _too_large(f"The office has more than {OFFICE_MAX_AREAS} areas", field="areas",
                             limit=OFFICE_MAX_AREAS)
        return definition

    def _stage_targets(self, definition: OfficeDefinition, *, into: str | None,
                       reimport: str | None) -> tuple[str, str]:
        if into is not None and reimport is not None:
            raise _invalid("Choose either an office to add an area to or an office to reimport", field="into")
        offices = self._read()
        if into is not None:
            target = offices.get(into)
            if target is None:
                raise self._not_installed(into)
            if len(definition.areas) != 1:
                raise ServiceError(INVALID_REQUEST, "To add to an office, the office.yaml must hold exactly one area",
                                   status=422, details={"reason": "office_single_area_required",
                                                        "areas": len(definition.areas)})
            area_id = definition.areas[0].id
            if any(area["id"] == area_id for area in target["definition"]["areas"]):
                raise _conflict(f"The office {into} already has the area {area_id}", "office_area_exists",
                                office=into, area=area_id)
            bundle = area_bundle_name(into, area_id)
            if self._taken(bundle, self._services.installed._read()):
                raise _conflict(f"Bundle name in use: {bundle}", "bundle_name_in_use", bundle=bundle)
            if len(target["definition"]["areas"]) + 1 > OFFICE_MAX_AREAS:
                raise _too_large(f"The office would have more than {OFFICE_MAX_AREAS} areas", field="areas",
                                 limit=OFFICE_MAX_AREAS)
            return "area", into
        if reimport is not None:
            previous = offices.get(reimport)
            if previous is None:
                raise self._not_installed(reimport)
            if definition.id != reimport:
                raise _conflict(f"The office.yaml is the office {definition.id}, not {reimport}", "office_id_mismatch",
                                office=reimport, found=definition.id)
            return "reimport", reimport
        if definition.id in offices:
            raise _conflict(f"Office already installed: {definition.id}", "office_exists", office=definition.id)
        return "install", definition.id

    @staticmethod
    def _code_files(root: Path, definition: OfficeDefinition) -> list[str]:
        found = []
        for area in definition.areas:
            folder = root.joinpath(*area.path.split("/"))
            for path in folder.rglob("*"):
                if (path.is_file() and "__pycache__" not in path.relative_to(folder).parts
                        and path.suffix.lower() not in CODE_FREE_SUFFIXES):
                    found.append(path.relative_to(root).as_posix())
        return sorted(found)

    def _require_stage_mode(self, root: Path, definition: OfficeDefinition) -> tuple[str, bool]:
        code_files = self._code_files(root, definition)
        level = "code" if code_files else "data"
        has_functions = any(Path(name).name == "functions.py" for name in code_files)
        if level == "code":
            self._services.gui_settings.require_advanced_mode("install_code")
            if has_functions:
                self._services.gui_settings.require_advanced_mode("trust_functions")
        return level, has_functions

    def _create_stage(self, *, folder: Path | None, data: bytes | None, filename: str,
                      into: str | None, reimport: str | None) -> dict[str, Any]:
        into = self._check_id(into, "into") if into is not None else None
        reimport = self._check_id(reimport, "reimport") if reimport is not None else None
        stage_id = uuid.uuid4().hex
        stage = self._stage_folder(stage_id)
        content = stage / "source"
        stage.mkdir(parents=True, exist_ok=False)
        roots = [str(stage), str(stage.resolve()), stage.as_posix(), stage.resolve().as_posix()]
        try:
            with self._staged(folder=folder, data=data) as prepared:
                definition = self._definition_for_stage(prepared)
                kind, office_id = self._stage_targets(definition, into=into, reimport=reimport)
                level, _ = self._require_stage_mode(prepared, definition)
                if level == "data":
                    self._check_areas(prepared, definition)
                shutil.copytree(prepared, content)
            fingerprint = BundleScanServices.fingerprint(content)
            metadata = {"stage_id": stage_id, "created_at": self.clock(), "kind": kind, "office_id": office_id,
                        "into": into, "reimport": reimport, "filename": str(filename or "")[:80],
                        "fingerprint": fingerprint, "approval_id": None}
            (stage / "stage.json").write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
        except Exception as exc:
            shutil.rmtree(stage, ignore_errors=True)
            translated = translate(exc)
            if translated is None:
                raise
            translated.message = _scrub(translated.message, roots)
            translated.details = _scrub(translated.details, roots)
            if translated is exc:
                raise
            raise translated from exc
        return self.card(stage_id)

    @service_errors
    def stage_zip(self, data: bytes, *, filename: str = "", into: str | None = None,
                  reimport: str | None = None) -> dict[str, Any]:
        return self._create_stage(folder=None, data=data, filename=filename, into=into, reimport=reimport)

    @service_errors
    def stage_folder(self, folder: Path, *, into: str | None = None,
                     reimport: str | None = None) -> dict[str, Any]:
        return self._create_stage(folder=folder, data=None, filename="", into=into, reimport=reimport)

    def _stage_changes(self, root: Path, definition: OfficeDefinition,
                       metadata: dict[str, Any]) -> dict[str, list[str]]:
        kind, office_id = metadata["kind"], metadata["office_id"]
        if kind == "install":
            return {"added": [area.id for area in definition.areas], "changed": [], "removed": [], "unchanged": []}
        if kind == "area":
            existing = self._read()[office_id]["definition"]["areas"]
            return {"added": [definition.areas[0].id], "changed": [], "removed": [],
                    "unchanged": [area["id"] for area in existing]}

        previous = self._read()[office_id]
        old_areas = {area["id"]: area for area in previous["definition"]["areas"]}
        registered = self._services.installed._read()
        added, changed, unchanged = [], [], []
        for area in definition.areas:
            bundle = area_bundle_name(office_id, area.id)
            package = registered.get(bundle)
            digest = BundleScanServices.fingerprint(root.joinpath(*area.path.split("/")))
            if area.id not in old_areas:
                added.append(area.id)
            elif (package is None or package.get("fingerprint") != digest
                  or self._services.installed._states.get(bundle, STATE_OK) != STATE_OK):
                changed.append(area.id)
            else:
                unchanged.append(area.id)
        kept = {area.id for area in definition.areas}
        removed = [area_id for area_id in old_areas if area_id not in kept]
        return {"added": added, "changed": changed, "removed": removed, "unchanged": unchanged}

    def _stage_scan_inputs(self, root: Path, definition: OfficeDefinition,
                           metadata: dict[str, Any]) -> tuple[str, dict[str, Path]]:
        kind, office_id = metadata["kind"], metadata["office_id"]
        staged = {area.id: root.joinpath(*area.path.split("/")) for area in definition.areas}
        if kind != "area":
            return metadata["fingerprint"], staged
        current = self._read()[office_id]
        registered = self._services.installed._read()
        folders = {}
        fingerprints = {}
        for area in current["definition"]["areas"]:
            bundle = area_bundle_name(office_id, area["id"])
            package = registered.get(bundle)
            if package is None or package.get("source") != SOURCE_OFFICE or package.get("office") != office_id:
                continue
            folder = self._services.bundle_path(bundle)
            if folder.is_dir():
                folders[area["id"]] = folder
                fingerprints[bundle] = BundleScanServices.fingerprint(folder)
        folders.update(staged)
        fingerprint = sha256(canonical_json({"office": current["fingerprint"],
                                             "areas": fingerprints,
                                             "staged": metadata["fingerprint"]}))
        return fingerprint, folders

    @staticmethod
    def _python_declarations(path: Path, label: str) -> list[str]:
        if not path.is_file():
            return []
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=label)
        except (OSError, SyntaxError):
            raise ServiceError(INVALID_DEFINITION, f"Invalid Python file in office: {label}", status=422,
                               details={"reason": "office_code_invalid", "file": label}) from None
        names = []
        for statement in tree.body:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(statement.name)
            if isinstance(statement, (ast.Assign, ast.AnnAssign)):
                targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
                if any(isinstance(target, ast.Name) and target.id == "OUTPUT_SCHEMAS" for target in targets):
                    value = statement.value
                    if isinstance(value, ast.Dict):
                        names.extend(key.value for key in value.keys if isinstance(key, ast.Constant)
                                     and isinstance(key.value, str))
        return sorted(set(names))

    @staticmethod
    def _area_members(folder: Path) -> list[dict[str, Any]]:
        team_path = folder / "team.yaml"
        if not team_path.is_file():
            return []
        raw = yaml.safe_load(team_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            return []
        assignments: dict[str, list[str]] = {}
        for assignment in raw.get("assignments") or []:
            if isinstance(assignment, dict) and isinstance(assignment.get("member"), str):
                role = assignment.get("role")
                if isinstance(role, str):
                    assignments.setdefault(assignment["member"], []).append(role)
        members = []
        for member in raw.get("members") or []:
            if not isinstance(member, dict):
                continue
            member_id = member.get("id")
            if not isinstance(member_id, str):
                continue
            roles = sorted(assignments.get(member_id, []))
            members.append({"id": member_id, "kind": member.get("kind"), "name": member.get("name") or
                            member.get("agent") or member_id, "role": ", ".join(roles) or None})
        return members

    def _area_declares(self, folder: Path, root: Path) -> dict[str, list[str]]:
        workflows = sorted(path.stem for path in (folder / "workflows").glob("*.yaml"))
        contracts: set[str] = set()
        data_contracts = folder / "contracts.yaml"
        if data_contracts.is_file():
            raw = yaml.safe_load(data_contracts.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict) and isinstance(raw.get("contracts"), dict):
                contracts.update(str(name) for name in raw["contracts"])
        contracts.update(self._python_declarations(folder / "contracts.py",
                                                   (folder / "contracts.py").relative_to(root).as_posix()))
        team = yaml.safe_load((folder / "team.yaml").read_text(encoding="utf-8")) if (folder / "team.yaml").is_file() else {}
        role_rows = team.get("roles", []) if isinstance(team, dict) else []
        roles = sorted(str(role["id"]) for role in role_rows
                       if isinstance(role, dict) and isinstance(role.get("id"), str))
        return {"workflows": workflows, "contracts": sorted(contracts), "roles": roles}

    def _stage_approval(self, stage_id: str, metadata: dict[str, Any], fingerprint: str, level: str,
                        risk_ack_required: bool) -> str:
        office_id = metadata["office_id"]
        destination = {"office": office_id, "kind": "office_install"}
        approvals = self._services.user_approvals
        current_id = metadata.get("approval_id")
        if isinstance(current_id, str):
            current = approvals.peek(current_id)
            current_location = current.get("location") if current is not None else None
            if (current is not None and current["state"] in {"pending", "approved"}
                    and current["kind"] == "office_install" and current["sha256"] == fingerprint
                    and current["destination"] == destination and isinstance(current_location, dict)
                    and current_location.get("stage_id") == stage_id):
                return current_id
        matches = [item for item in approvals._all().values()
                   if item["kind"] == "office_install" and item["sha256"] == fingerprint
                   and item["destination"] == destination
                   and isinstance(item.get("location"), dict)
                   and item["location"].get("stage_id") == stage_id
                   and item["state"] in {"pending", "approved"}]
        if matches:
            approval_id = matches[-1]["id"]
        else:
            approval = approvals.request(
                kind="office_install", destination=destination,
                location={"stage_id": stage_id, "level": level, "risk_ack_required": risk_ack_required},
                sha256=fingerprint)
            approval_id = approval["id"]
        metadata["approval_id"] = approval_id
        stage = self._stage_folder(stage_id)
        (stage / "stage.json").write_text(json.dumps(metadata, sort_keys=True), encoding="utf-8")
        return approval_id

    @service_errors
    def card(self, stage_id: str) -> dict[str, Any]:
        root, metadata = self._stage_record(stage_id)
        definition = self._definition_for_stage(root)
        if BundleScanServices.fingerprint(root) != metadata.get("fingerprint"):
            raise self._install_error("office_changed_since_card", "The office changed after its card was prepared",
                                      stage_id=stage_id)
        kind, office_id = self._stage_targets(definition, into=metadata.get("into"),
                                              reimport=metadata.get("reimport"))
        if kind != metadata.get("kind") or office_id != metadata.get("office_id"):
            raise self._install_error("office_changed_since_card", "The office preparation no longer matches its card",
                                      stage_id=stage_id)
        level, _ = self._require_stage_mode(root, definition)
        changes = self._stage_changes(root, definition, metadata)
        requirements = {item["id"]: item for item in self.requirements_for(definition)["areas"]}
        if kind == "reimport":
            old = _office_of_record(self._read()[office_id]["definition"])
            previous_requirements = {item["id"]: item for item in self.requirements_for(old)["areas"]}
            requirements.update({area_id: previous_requirements[area_id] for area_id in changes["removed"]})
        visible_ids = [*changes["added"], *changes["changed"], *changes["removed"]]
        staged_areas = {area.id: area for area in definition.areas}
        folders: dict[str, Path] = {}
        for area in definition.areas:
            folders[area.id] = root.joinpath(*area.path.split("/"))
        scan_fingerprint, scan_folders = self._stage_scan_inputs(root, definition, metadata)
        scan = self._services.bundle_scans.scan_office(office_id, scan_fingerprint, scan_folders)
        areas = []
        for area_id in visible_ids:
            area = staged_areas.get(area_id)
            change = ("added" if area_id in changes["added"] else "changed"
                      if area_id in changes["changed"] else "removed")
            if area is not None:
                folder = folders[area_id]
                data = {"id": area.id, "name": area.name, "sensitivity": area.sensitivity,
                        "local_only": area.local_only, "requires": requirements.get(area_id, {}),
                        "members": self._area_members(folder), "declares": self._area_declares(folder, root)}
            else:
                old_record = self._read()[office_id]["definition"]
                record = next(item for item in old_record["areas"] if item["id"] == area_id)
                bundle = area_bundle_name(office_id, area_id)
                folder = self._services._bundles.get(bundle)
                data = {"id": area_id, "name": record["name"], "sensitivity": record.get("sensitivity"),
                        "local_only": bool(record.get("local_only")), "requires": requirements.get(area_id, {}),
                        "members": self._area_members(folder) if folder is not None else [],
                        "declares": self._area_declares(folder, folder) if folder is not None else
                        {"workflows": [], "contracts": [], "roles": []}}
            areas.append({"id": area_id, "name": data["name"], "change": change, "members": data["members"],
                          "requires": data["requires"], "sensitivity": data["sensitivity"],
                          "local_only": data["local_only"], "declares": data["declares"]})
        code_files = self._code_files(root, definition)
        functions = sorted({name for area in definition.areas
                            for name in self._python_declarations(
                                root.joinpath(*area.path.split("/")) / "functions.py",
                                (root.joinpath(*area.path.split("/")) / "functions.py").relative_to(root).as_posix())})
        code = {"present": level == "code", "files": code_files[:OFFICE_CODE_FILES_SHOWN], "functions": functions}
        allowed = {"safe", "caution", "off", "not_installed"}
        current_approval = (self._services.user_approvals.peek(metadata["approval_id"])
                            if isinstance(metadata.get("approval_id"), str) else None)
        current_location = current_approval.get("location") if current_approval is not None else None
        risk_ack_required = (level == "code" or any(item["state"] not in allowed for item in scan["areas"].values())
                             or isinstance(current_location, dict)
                             and current_location.get("risk_ack_required") is True)
        approval_id = self._stage_approval(stage_id, metadata, metadata["fingerprint"], level, risk_ack_required)
        target = self._read().get(office_id) if kind in {"area", "reimport"} else None
        shown_office = ({"id": office_id, "name": target["definition"]["name"],
                         "description": target["definition"].get("description")}
                        if target is not None else {"id": definition.id, "name": definition.name,
                                                    "description": definition.description})
        return {"stage_id": stage_id, "kind": kind, "office": shown_office, "into": metadata.get("into"),
                "level": level, "fingerprint": metadata["fingerprint"], "notice": OFFICE_NOTICE, "areas": areas,
                "code": code, "scan": scan, "approval_id": approval_id,
                "risk_ack_required": risk_ack_required}

    def _approve_stage_teams(self, office_id: str, area_ids: list[str], infos: dict[str, dict[str, Any]], *,
                             actor: str, channel: str, fingerprint: str, approval_id: str,
                             warnings: list[dict[str, str]]) -> list[dict[str, Any]]:
        note = f"aprobado al instalar la oficina {office_id} {fingerprint} (aprobación {approval_id})"
        result = []
        for area_id in area_ids:
            bundle = area_bundle_name(office_id, area_id)
            state = self._propose(bundle, infos[area_id]["team_digest"], actor=actor, channel=channel,
                                  warnings=warnings)
            if state is None:
                continue
            if state["status"] == "proposed":
                approved = self._services.teams.approve(state["version_id"], actor=actor, channel=channel, note=note)
                state = {"bundle": bundle, "version_id": approved["id"], "status": approved["status"]}
            result.append(state)
        return result

    def _seed_office_scan_cache(self, office_id: str, folders: dict[str, Path], scan: dict[str, Any], *,
                                approved_area_ids: set[str], risk_ack: bool) -> None:
        for area_id, folder in folders.items():
            bundle = area_bundle_name(office_id, area_id)
            fingerprint = BundleScanServices.fingerprint(folder)
            area_scan = scan["areas"][area_id]
            previous = self._services.bundle_scans._cached(bundle, fingerprint) or {}
            approved = area_id in approved_area_ids
            cached = {"fingerprint": fingerprint,
                      "scan": {"state": area_scan["state"], "findings": area_scan.get("findings", []),
                               "coverage": area_scan.get("coverage", {})},
                      "load_approved": approved or previous.get("load_approved") is True}
            if previous.get("risk_ack") is True or approved and risk_ack:
                cached["risk_ack"] = True
            if area_scan["state"] in {"off", "not_installed"}:
                self._services.bundle_scans._unscanned[(bundle, fingerprint)] = cached
            else:
                self._services.bundle_scans._write_cache(bundle, fingerprint, cached)

    @staticmethod
    def _scan_wait_error(scan: dict[str, Any]) -> ServiceError | None:
        if scan["state"] == "pending":
            return ServiceError("bundle_scan_pending", "Approve the office OSV query in the GUI", status=409,
                                details={"approval_id": scan.get("approval_id"), "location": "/gui/"})
        if scan["state"] in {"running", "unscanned"}:
            return ServiceError("bundle_scan_running", "The office scan is not finished", status=409,
                                details={"office": scan.get("office")})
        return None

    def _validate_stage_areas(self, root: Path, definition: OfficeDefinition) -> dict[str, dict[str, Any]]:
        roots = [str(root), str(root.resolve()), root.as_posix(), root.resolve().as_posix()]
        try:
            return self._check_areas(root, definition)
        except Exception as exc:
            translated = translate(exc)
            if translated is None:
                raise
            translated.message = _scrub(translated.message, roots)
            translated.details = _scrub(translated.details, roots)
            if translated is exc:
                raise
            raise translated from exc

    def _stage_area_infos(self, root: Path, definition: OfficeDefinition, *, level: str
                          ) -> dict[str, dict[str, Any]]:
        if level == "data":
            return self._validate_stage_areas(root, definition)
        roots = [str(root), str(root.resolve()), root.as_posix(), root.resolve().as_posix()]
        try:
            infos = {}
            for area in definition.areas:
                folder = root.joinpath(*area.path.split("/"))
                loaded = load_bundle(folder, self._services.settings)
                digest = team_sha256(loaded.team) if loaded.team is not None else None
                if area.team_sha256 is not None and area.team_sha256 != digest:
                    raise _conflict(f"The team of the area {area.id} is not the one the office declares",
                                    "office_team_mismatch", area=area.id)
                infos[area.id] = {"folder": folder, "fingerprint": BundleScanServices.fingerprint(folder),
                                  "team_digest": digest, "has_team": loaded.team is not None}
            return infos
        except Exception as exc:
            translated = translate(exc)
            if translated is None:
                raise
            translated.message = _scrub(translated.message, roots)
            translated.details = _scrub(translated.details, roots)
            if translated is exc:
                raise
            raise translated from exc

    def _validate_installed_code(self, definition: OfficeDefinition, office_id: str) -> None:
        for area in definition.areas:
            folder = self._services.bundle_path(area_bundle_name(office_id, area.id))
            if package_level(folder) == "code":
                self._services.definitions.validate_bundle(area_bundle_name(office_id, area.id))

    def _install_new_stage(self, root: Path, definition: OfficeDefinition, metadata: dict[str, Any],
                           infos: dict[str, dict[str, Any]], card: dict[str, Any], *, actor: str,
                           channel: str, approval_id: str) -> dict[str, Any]:
        office_id = definition.id
        offices = self._read()
        if office_id in offices:
            raise _conflict(f"Office already installed: {office_id}", "office_exists", office=office_id)
        installed = self._services.installed
        registered = installed._read()
        bundles = {area.id: area_bundle_name(office_id, area.id) for area in definition.areas}
        for bundle in bundles.values():
            if self._taken(bundle, registered):
                raise _conflict(f"Bundle name in use: {bundle}", "bundle_name_in_use", bundle=bundle)
        trusted = {bundles[area.id] for area in definition.areas
                   if (root.joinpath(*area.path.split("/")) / "functions.py").is_file()}
        changes = [{"op": "install", "name": bundles[area.id],
                    "folder": str(root.joinpath(*area.path.split("/")))} for area in definition.areas]
        installed.apply_batch(changes, actor=actor, channel=channel, source=SOURCE_OFFICE, office=office_id,
                              allow_code=card["level"] == "code", trusted_functions=trusted,
                              expected_fingerprints={bundles[area.id]: infos[area.id]["fingerprint"]
                                                     for area in definition.areas})
        scan_folders = {area.id: root.joinpath(*area.path.split("/")) for area in definition.areas}
        self._seed_office_scan_cache(office_id, scan_folders, card["scan"],
                                     approved_area_ids={area.id for area in definition.areas},
                                     risk_ack=card["risk_ack_required"])
        for bundle in trusted:
            installed.load_functions(bundle)
        if card["level"] == "code":
            self._validate_installed_code(definition, office_id)
        warnings = list(definition.warnings)
        teams = self._approve_stage_teams(office_id, [area.id for area in definition.areas], infos,
                                          actor=actor, channel=channel, fingerprint=card["fingerprint"],
                                          approval_id=approval_id, warnings=warnings)
        version = self._next_version(office_id, offices)
        copy = self._save_copy(office_id, version, (root / "office.yaml").read_bytes())
        entry = self._entry(definition, _record_of(definition), version=version, actor=actor, channel=channel,
                            registered=installed._read())
        offices[office_id] = entry
        self._write(offices)
        self._event("office.installed", office_id, actor=actor, channel=channel, version=version,
                    areas=[area.id for area in definition.areas], approval_id=approval_id)
        return {"office": self._public(entry, installed._read()), "teams": teams, "warnings": warnings,
                "changes": {"added": [area.id for area in definition.areas], "changed": [], "removed": [],
                            "unchanged": []}}

    def _install_reimport_stage(self, root: Path, definition: OfficeDefinition, metadata: dict[str, Any],
                                infos: dict[str, dict[str, Any]], card: dict[str, Any], *, actor: str,
                                channel: str, approval_id: str) -> dict[str, Any]:
        office_id = metadata["office_id"]
        offices = self._read()
        previous = offices.get(office_id)
        if previous is None:
            raise self._not_installed(office_id)
        installed = self._services.installed
        registered = installed._read()
        old_areas = {area["id"]: area for area in previous["definition"]["areas"]}
        added, changed, unchanged = [], [], []
        for area in definition.areas:
            bundle = area_bundle_name(office_id, area.id)
            package = registered.get(bundle)
            if area.id not in old_areas:
                if self._taken(bundle, registered):
                    raise _conflict(f"Bundle name in use: {bundle}", "bundle_name_in_use", bundle=bundle)
                added.append(area.id)
            elif (package is None or package.get("fingerprint") != infos[area.id]["fingerprint"]
                  or installed._states.get(bundle, STATE_OK) != STATE_OK):
                changed.append(area.id)
            else:
                unchanged.append(area.id)
        kept = {area.id for area in definition.areas}
        removed = [area_id for area_id in old_areas if area_id not in kept]
        batch = ([{"op": "install", "name": area_bundle_name(office_id, area_id),
                   "folder": str(root.joinpath(*next(area.path for area in definition.areas
                                                     if area.id == area_id).split("/")))} for area_id in added]
                 + [{"op": "reinstall", "name": area_bundle_name(office_id, area_id),
                     "folder": str(root.joinpath(*next(area.path for area in definition.areas
                                                       if area.id == area_id).split("/")))} for area_id in changed]
                 + [{"op": "remove", "name": area_bundle_name(office_id, area_id)} for area_id in removed
                    if area_bundle_name(office_id, area_id) in registered])
        changed_areas = set(added) | set(changed)
        trusted = {area_bundle_name(office_id, area.id) for area in definition.areas
                   if area.id in changed_areas
                   and (root.joinpath(*area.path.split("/")) / "functions.py").is_file()}
        if batch:
            installed.apply_batch(batch, actor=actor, channel=channel, source=SOURCE_OFFICE, office=office_id,
                                  allow_code=card["level"] == "code", trusted_functions=trusted,
                                  expected_fingerprints={area_bundle_name(office_id, area_id):
                                                         infos[area_id]["fingerprint"]
                                                         for area_id in changed_areas})
        scan_folders = {area.id: root.joinpath(*area.path.split("/")) for area in definition.areas}
        self._seed_office_scan_cache(office_id, scan_folders, card["scan"],
                                     approved_area_ids={area.id for area in definition.areas},
                                     risk_ack=card["risk_ack_required"])
        for bundle in trusted:
            installed.load_functions(bundle)
        if card["level"] == "code":
            self._validate_installed_code(definition, office_id)
        warnings = list(definition.warnings)
        teams = []
        for area in definition.areas:
            bundle = area_bundle_name(office_id, area.id)
            if area.id in unchanged:
                state = self._team_state(bundle)
                state = {"bundle": bundle, **state} if state["version_id"] else None
            else:
                state = self._approve_stage_teams(office_id, [area.id], infos, actor=actor, channel=channel,
                                                  fingerprint=card["fingerprint"], approval_id=approval_id,
                                                  warnings=warnings)
                state = state[0] if state else None
            if state:
                teams.append(state)
        record = _record_of(definition)
        levels = self._highest_levels(previous, record)
        entry = previous
        if batch or record != previous["definition"] or levels != previous.get("privacy"):
            version = self._next_version(office_id, offices)
            self._save_copy(office_id, version, (root / "office.yaml").read_bytes())
            entry = self._entry(definition, record, version=version, actor=actor, channel=channel,
                                registered=installed._read())
            if levels:
                entry["privacy"] = levels
            offices[office_id] = entry
            self._write(offices)
            self._event("office.reimported", office_id, actor=actor, channel=channel, version=version,
                        added=added, changed=changed, removed=removed, approval_id=approval_id)
        return {"office": self._public(entry, installed._read()), "teams": teams, "warnings": warnings,
                "changes": {"added": added, "changed": changed, "removed": removed, "unchanged": unchanged}}

    def _install_area_stage(self, root: Path, definition: OfficeDefinition, metadata: dict[str, Any],
                            infos: dict[str, dict[str, Any]], card: dict[str, Any], *, actor: str,
                            channel: str, approval_id: str) -> dict[str, Any]:
        office_id = metadata["office_id"]
        offices = self._read()
        previous = offices.get(office_id)
        if previous is None:
            raise self._not_installed(office_id)
        if len(definition.areas) != 1:
            raise ServiceError(INVALID_REQUEST, "To add to an office, the office.yaml must hold exactly one area",
                               status=422, details={"reason": "office_single_area_required",
                                                    "areas": len(definition.areas)})
        area = definition.areas[0]
        area_id = area.id
        record = json.loads(json.dumps(previous["definition"]))
        if any(item["id"] == area_id for item in record["areas"]):
            raise _conflict(f"The office {office_id} already has the area {area_id}", "office_area_exists",
                            office=office_id, area=area_id)
        if len(record["areas"]) + 1 > OFFICE_MAX_AREAS:
            raise _too_large(f"The office would have more than {OFFICE_MAX_AREAS} areas", field="areas",
                             limit=OFFICE_MAX_AREAS)
        installed = self._services.installed
        bundle = area_bundle_name(office_id, area_id)
        if self._taken(bundle, installed._read()):
            raise _conflict(f"Bundle name in use: {bundle}", "bundle_name_in_use", bundle=bundle)
        incoming = _record_of(definition)
        known = {item["id"] for item in record["areas"]} | {area_id}
        record["areas"].append(incoming["areas"][0])
        extra: list[dict[str, str]] = []
        for relation in incoming["relations"]:
            if relation["from"] in known and relation["to"] in known:
                if relation not in record["relations"]:
                    record["relations"].append(relation)
            else:
                extra.append(_warning("office_relation_unknown_area", f"relations[{relation['from']}->{relation['to']}]",
                                      f"Relation references an area that {office_id} does not have; it was not added"))
        installed.apply_batch([{"op": "install", "name": bundle,
                                "folder": str(root.joinpath(*area.path.split("/")))}],
                              actor=actor, channel=channel, source=SOURCE_OFFICE, office=office_id,
                              allow_code=card["level"] == "code",
                              trusted_functions={bundle} if (root.joinpath(*area.path.split("/")) /
                                                             "functions.py").is_file() else set(),
                              expected_fingerprints={bundle: infos[area_id]["fingerprint"]})
        _, scan_folders = self._stage_scan_inputs(root, definition, metadata)
        self._seed_office_scan_cache(office_id, scan_folders, card["scan"],
                                     approved_area_ids=set(scan_folders),
                                     risk_ack=card["risk_ack_required"])
        if (root.joinpath(*area.path.split("/")) / "functions.py").is_file():
            installed.load_functions(bundle)
        if card["level"] == "code":
            self._validate_installed_code(definition, office_id)
        merged = _office_of_record(record)
        warnings: list[dict[str, str]] = list(merged.warnings)
        team_list = self._approve_stage_teams(office_id, [area_id], infos, actor=actor, channel=channel,
                                              fingerprint=card["fingerprint"], approval_id=approval_id,
                                              warnings=warnings)
        version = self._next_version(office_id, offices)
        self._save_copy(office_id, version, _yaml_of_record(record))
        entry = self._entry(merged, record, version=version, actor=actor, channel=channel,
                            registered=installed._read())
        self._carry_privacy(entry, previous)
        offices[office_id] = entry
        self._write(offices)
        self._event("office.reimported", office_id, actor=actor, channel=channel, version=version, added=[area_id],
                    changed=[], removed=[], approval_id=approval_id)
        unchanged = [item["id"] for item in record["areas"] if item["id"] != area_id]
        return {"office": self._public(entry, installed._read()), "teams": team_list,
                "warnings": [*merged.warnings, *extra, *warnings],
                "changes": {"added": [area_id], "changed": [], "removed": [], "unchanged": unchanged}}

    @service_errors
    def install_staged(self, stage_id: str, *, approval_id: str, actor: str = "user:operator",
                       channel: str = "gui") -> dict[str, Any]:
        from .projects import _user_actor

        actor = _user_actor(actor)
        if not isinstance(approval_id, str) or not approval_id:
            raise self._install_error("office_install_not_approved", "An approved, unused office card is required")
        try:
            root, metadata = self._stage_record(stage_id)
        except ServiceError as exc:
            approval = self._services.user_approvals.peek(approval_id) if isinstance(approval_id, str) else None
            if (exc.details.get("reason") == "office_stage_not_found" and approval is not None
                    and approval.get("kind") == "office_install" and approval.get("state") == "consumed"):
                raise self._install_error("office_install_not_approved",
                                          "The office install approval is already used", approval_id=approval_id) from None
            raise exc
        if BundleScanServices.fingerprint(root) != metadata.get("fingerprint"):
            raise self._install_error("office_changed_since_card", "The office changed after its card was prepared",
                                      stage_id=stage_id)
        existing = self._services.user_approvals.peek(approval_id)
        destination = {"office": metadata.get("office_id"), "kind": "office_install"}
        location = existing.get("location") if existing is not None else None
        if (existing is None or existing.get("kind") != "office_install" or existing.get("state") != "approved"
                or existing.get("sha256") != metadata.get("fingerprint")
                or existing.get("destination") != destination or not isinstance(location, dict)
                or location.get("stage_id") != stage_id):
            raise self._install_error("office_install_not_approved", "An approved, unused office card is required",
                                      approval_id=approval_id)
        definition = self._definition_for_stage(root)
        level, has_functions = self._require_stage_mode(root, definition)
        card = self.card(stage_id)
        if card["approval_id"] != approval_id:
            raise self._install_error("office_install_not_approved", "An approved, unused office card is required",
                                      approval_id=approval_id)
        scan = card["scan"]
        wait_error = self._scan_wait_error(scan)
        if wait_error is not None:
            raise wait_error
        if BundleScanServices.fingerprint(root) != metadata.get("fingerprint"):
            raise self._install_error("office_changed_since_card", "The office changed after its card was prepared",
                                      stage_id=stage_id)
        if location.get("risk_ack_required") is True and existing.get("risk_ack") is not True:
            raise ServiceError("risk_ack_required", "Acknowledge the office install risk", status=422)
        if not self._services.user_approvals.consume(approval_id, sha256=metadata["fingerprint"],
                                                     destination=destination):
            raise self._install_error("office_install_not_approved", "An approved, unused office card is required",
                                      approval_id=approval_id)
        snapshot = self._services.installed._copy_to_staging(root)
        try:
            if BundleScanServices.fingerprint(snapshot) != metadata["fingerprint"]:
                raise self._install_error("office_changed_since_card", "The office changed after its card was prepared",
                                          stage_id=stage_id)
            verified_level, has_functions = self._require_stage_mode(snapshot, definition)
            if card["level"] != level or verified_level != level:
                raise self._install_error("office_changed_since_card", "The office changed after its card was prepared",
                                          stage_id=stage_id)
            infos = self._stage_area_infos(snapshot, definition, level=level)
            if level == "code" and has_functions:
                for area in definition.areas:
                    folder = snapshot.joinpath(*area.path.split("/"))
                    if (folder / "functions.py").is_file():
                        self._services.gui_settings.require_advanced_mode("trust_functions")
            kind = metadata["kind"]
            if kind == "install":
                result = self._install_new_stage(snapshot, definition, metadata, infos, card, actor=actor,
                                                 channel=channel, approval_id=approval_id)
            elif kind == "reimport":
                result = self._install_reimport_stage(snapshot, definition, metadata, infos, card, actor=actor,
                                                      channel=channel, approval_id=approval_id)
            elif kind == "area":
                result = self._install_area_stage(snapshot, definition, metadata, infos, card, actor=actor,
                                                  channel=channel, approval_id=approval_id)
            else:
                raise self._install_error("office_stage_not_found", "Office preparation not found")
        finally:
            shutil.rmtree(snapshot, ignore_errors=True)
        shutil.rmtree(self._stage_folder(stage_id), ignore_errors=True)
        return result

    @service_errors
    def discard_stage(self, stage_id: str) -> None:
        stage = self._stage_folder(stage_id)
        if not stage.exists():
            return
        try:
            _, metadata = self._stage_record(stage_id)
        except ServiceError as exc:
            if exc.details.get("reason") != "office_stage_expired":
                raise
            return
        self._reject_stage_approval(metadata)
        shutil.rmtree(stage, ignore_errors=True)

    # --- office scene (ESC-T01) ----------------------------------------------

    @service_errors
    def scene(self, office_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        """Return the versioned scene without loading bundle code or creating an area runtime."""
        office_id = self._check_id(office_id, "office_id")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        else:
            current = current.astimezone(timezone.utc)
        since = (current - timedelta(hours=SCENE_WINDOW_HOURS)).isoformat()
        with self._lock:
            entry = self._read().get(office_id)
            if entry is None:
                raise ServiceError(SERVICE_NOT_FOUND, f"Office not found: {office_id}", status=404,
                                   details={"office": office_id})
            definition = _office_of_record(entry["definition"])
            registered = self._services.installed._read()
            office = self._public(entry, registered)

        rows = list(getattr(self._services.connections, "_last", {}).values())
        requirements: dict[str, dict[str, Any]] = {}
        if rows:
            requirements = {
                item["id"]: item
                for item in office_requirements(definition, self.requirements_candidates(rows=rows))["areas"]
            }
        scene_areas = []
        for area in office["areas"]:
            bundle = area["bundle"]
            package_state = area["package_state"]
            team = None
            if package_state == STATE_OK:
                version = self._services.teams.approved(bundle)
                if version is None:
                    versions = self._services.teams.list(bundle)["versions"]
                    version = versions[-1] if versions else None
                if version is not None:
                    team = version["team"]

            degraded = []
            if package_state == "changed":
                degraded.append({"code": "installed_bundle_changed",
                                 "fix": {"label": "Reimportar la oficina", "href": None, "action": "reimport"}})
            elif package_state == STATE_MISSING:
                degraded.append({"code": "bundle_not_installed",
                                 "fix": {"label": "Reimportar la oficina", "href": None, "action": "reimport"}})

            team_state = area.get("team") or {}
            if team_state.get("status") is not None and team_state.get("status") != "approved":
                version_id = team_state.get("version_id")
                degraded.append({"code": "team_not_approved",
                                 "fix": {"label": "Aprobar equipo",
                                         "href": f"#new/team/{version_id}" if version_id else None,
                                         "action": None}})
            requirement = requirements.get(area["id"], {})
            if requirement.get("missing"):
                degraded.append({"code": "requirement_missing",
                                 "fix": {"label": "Conectar un agente", "href": "#apps", "action": None}})

            runs = self._services.runs.recent_runs(bundle, since=since, limit=SCENE_RUNS_MAX)
            try:
                step_agents = _area_step_agents(self._services.bundle_path(bundle)) if package_state == STATE_OK else {}
            except ServiceError:
                step_agents = {}
            scene_areas.append({
                **area,
                "step_agents": step_agents,
                "data_class": self.area_class(bundle),
                "degraded": degraded,
                "team": team,
                "runs": runs,
                "truncated": len(runs) >= SCENE_RUNS_MAX,
            })

        office_group = office_group_id(office_id)
        proposals = self._services.plan_proposals.list(group_id=office_group)
        scene_proposals = []
        for proposal in proposals:
            if not is_office_group(proposal["group_id"], office_id):
                continue
            task_views = {}
            if proposal["status"] == "approved":
                task_views = {
                    task["id"]: task
                    for task in self._services.tasks.list_tasks(group_id=proposal["group_id"])
                }
            # PlanServices.status reads every run runtime; the scene uses task records directly to stay read-only.
            scene_proposals.append({**proposal, "scene_tasks": task_views})

        return build_scene(office, scene_areas, scene_proposals, now=current)

    # --- the example office (MVP-T01) ----------------------------------------

    @service_errors
    def example_available(self) -> bool:
        """Whether the example office can be copied from this source checkout."""
        if not IN_CHECKOUT:
            return False
        folder = PROJECT_ROOT / SHIPPED_OFFICES["cafeteria"]
        if not folder.is_dir():
            return False
        with self._lock:
            return "cafeteria" not in self._read()

    @service_errors
    def install_example(self, *, actor: str = "user:gui", channel: str = "gui") -> dict[str, Any]:
        """Copy the example office into the installation; nothing is installed at startup."""
        folder = PROJECT_ROOT / SHIPPED_OFFICES["cafeteria"]
        if not IN_CHECKOUT or not folder.is_dir():
            raise _conflict("The example office is only available in a checkout of the product",
                            "example_not_available", office="cafeteria")
        return self.import_folder(folder, actor=actor, channel=channel)
