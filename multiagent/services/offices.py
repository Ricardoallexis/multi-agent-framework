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

import json
import os
import re
import shutil
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

import yaml

from ..bundles import load_bundle
from ..db.database import utcnow
from ..external_output import canonical_json, sha256
from ..offices.schema import (OFFICE_ID_PATTERN, OFFICE_SCHEMA, OfficeDefinition, OfficeDefinitionError,
                              area_bundle_name, load_office)
from ..teams.digest import team_sha256
from .bundle_scans import BundleScanServices
from .errors import INVALID_DEFINITION, INVALID_REQUEST, ServiceError, service_errors, translate
from .installed import (CODE_FREE_SUFFIXES, SOURCE_OFFICE, STATE_MISSING, STATE_OK, ZIP_EXTRACT_MAX_BYTES, ZIP_MAX_FILES,
                        _conflict, _invalid, extract_zip_safely)

if TYPE_CHECKING:
    from . import ApplicationServices

OFFICES_FILE = "offices.json"
OFFICES_SCHEMA = "maf.installed-offices/1"
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
            areas.append({"id": area["id"], "name": area["name"], "bundle": bundle, "sensitivity": area.get("sensitivity"),
                          "local_only": bool(area.get("local_only")), "requires": list(area.get("requires") or []),
                          "package_state": package_state, "team": self._team_state(bundle)})
        return {"id": office_id, "name": definition["name"], "description": definition.get("description"),
                "version": entry.get("version"), "fingerprint": entry.get("fingerprint"),
                "installed_at": entry.get("installed_at"), "installed_by": entry.get("installed_by"),
                "channel": entry.get("channel"), "level": "data", "local_only": bool(definition.get("local_only")),
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
            self._services.installed._validate(folder)   # load_bundle and every workflow and the team, as bundle-validate
            team = load_bundle(folder, settings).team
            digest = team_sha256(team) if team is not None else None
            if area.team_sha256 is not None and area.team_sha256 != digest:
                raise _conflict(f"The team of the area {area.id} is not the one the office declares",
                                "office_team_mismatch", area=area.id)
            infos[area.id] = {"folder": folder, "fingerprint": BundleScanServices.fingerprint(folder),
                              "team_digest": digest, "has_team": team is not None}
        return infos

    def _taken(self, bundle: str, registered: dict[str, dict[str, Any]]) -> bool:
        return bundle in registered or bundle in self._services._bundles

    # --- teams, version copies and events -----------------------------------

    def _propose(self, bundle: str, digest: str | None, *, actor: str, channel: str,
                 warnings: list[dict[str, str]]) -> dict[str, Any] | None:
        """Propose the team of a package (never approve it). ``no_changes`` is not an error: the approved one stays."""
        if digest is None:
            return None
        teams = self._services.teams
        rows = teams.store.list(bundle)
        if rows and rows[-1]["sha256"] == digest and rows[-1]["status"] in ("proposed", "approved"):
            return {"bundle": bundle, "version_id": rows[-1]["id"], "status": rows[-1]["status"]}
        try:
            version = teams.propose(bundle, actor=actor, channel=channel)
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
                              actor=actor, channel=channel, source=SOURCE_OFFICE, office=office.id)
        copy: Path | None = None
        try:
            warnings = list(office.warnings)
            teams = [state for area in office.areas for state in [self._propose(
                bundles[area.id], infos[area.id]["team_digest"], actor=actor, channel=channel, warnings=warnings)] if state]
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
                              actor=actor, channel=channel, source=SOURCE_OFFICE, office=into)
        copy: Path | None = None
        try:
            warnings: list[dict[str, str]] = []
            team = self._propose(bundle, infos[area.id]["team_digest"], actor=actor, channel=channel, warnings=warnings)
            definition = _office_of_record(record)
            version = self._next_version(into, offices)
            copy = self._save_copy(into, version, _yaml_of_record(record))
            entry = self._entry(definition, record, version=version, actor=actor, channel=channel,
                                registered=installed._read())
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
                installed.apply_batch(batch, actor=actor, channel=channel, source=SOURCE_OFFICE, office=office_id)
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
                                              warnings=warnings)
                    if state:
                        teams.append(state)
                record = _record_of(office)
                entry = previous
                if batch or record != previous["definition"]:
                    version = self._next_version(office_id, offices)
                    copy = self._save_copy(office_id, version, (root / "office.yaml").read_bytes())
                    entry = self._entry(office, record, version=version, actor=actor, channel=channel,
                                        registered=installed._read())
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
