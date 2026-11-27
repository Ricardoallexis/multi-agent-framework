# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Allow-listed office exports and their import manifest (OFI-T05)."""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from ..db.database import utcnow
from ..secret_gate import scan_text
from ..services.errors import INVALID_REQUEST, ServiceError
from ..services.installed import CODE_FREE_SUFFIXES
from ..teams.digest import team_sha256
from ..teams.schema import Team
from .schema import OfficeDefinition
from .package_checks import excluded_path

EXPORT_SCHEMA = "maf.office-export/1"
MANIFEST_FILE = "manifest.json"
AREA_ALLOW = ("workflows/*.yaml", "prompts/**", "catalog/agents.yaml", "team.yaml", "contracts.yaml", "README.md",
              "samples/**")
CODE_FILES = ("contracts.py", "functions.py")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _area_file_allowed(path: str) -> bool:
    if excluded_path(path):
        return False
    if path.startswith("workflows/"):
        return path.count("/") == 1 and path.endswith(".yaml")
    if path.startswith("prompts/") or path.startswith("samples/"):
        return True
    return path in {"catalog/agents.yaml", "team.yaml", "contracts.yaml", "README.md"}


def _team_digest(content: bytes | None) -> str | None:
    if content is None:
        return None
    try:
        raw = yaml.safe_load(content)
        if not isinstance(raw, dict):
            raise ValueError("team.yaml must contain a mapping")
        team = Team.model_validate(raw, context={"skip_team_semantics": True})
    except (yaml.YAMLError, ValueError):
        raise ServiceError(INVALID_REQUEST, "An installed area's team cannot be exported", status=422,
                           details={"reason": "office_export_invalid_team"}) from None
    return team_sha256(team)


def _load_yaml(content: bytes, path: str) -> Any:
    try:
        return yaml.safe_load(content)
    except (yaml.YAMLError, ValueError):
        raise ServiceError(INVALID_REQUEST, "An installed YAML file cannot be exported", status=422,
                           details={"reason": "office_export_invalid_yaml", "path": path}) from None


def _text_findings(files: dict[str, bytes]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for path, content in sorted(files.items()):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise ServiceError(INVALID_REQUEST, "An exported text file is not valid UTF-8", status=422,
                               details={"reason": "office_export_invalid_text", "path": path}) from None
        found.extend({"path": path, "line": text.count("\n", 0, start) + 1, "type": kind}
                     for start, _, kind in scan_text(text))
    return found


def portable_files(files: dict[str, bytes], definition: OfficeDefinition, *, include_code: bool = False,
                   area: str | None = None) -> dict[str, bytes]:
    """Select only portable files from the installed-copy snapshot."""
    selected = {}
    for item in definition.areas:
        if area is not None and item.id != area:
            continue
        prefix = item.path.rstrip("/") + "/"
        for source_path, content in files.items():
            if not source_path.startswith(prefix):
                continue
            relative = source_path[len(prefix):]
            if excluded_path(relative):
                continue
            suffix_allowed = Path(relative).suffix.lower() in CODE_FREE_SUFFIXES
            if (_area_file_allowed(relative) and suffix_allowed) or (include_code and relative in CODE_FILES):
                selected[f"areas/{item.id}/{relative}"] = content
    return selected


def build_export(files: dict[str, bytes], definition: OfficeDefinition, *, include_code: bool = False,
                 area: str | None = None, omit_paths: frozenset[str] = frozenset()) -> tuple[bytes, dict[str, Any]]:
    """Build an office or area archive from supplied file bytes; this function never reads from disk."""
    exported_areas = [item for item in definition.areas if area is None or item.id == area]
    if not exported_areas:
        raise ServiceError(INVALID_REQUEST, f"Area not found: {area}", status=422,
                           details={"reason": "office_area_not_found", "area": area})

    selected: dict[str, bytes] = {}
    omitted: list[dict[str, str]] = []
    area_digests: list[dict[str, str | None]] = []
    area_records = []
    for item in exported_areas:
        prefix = item.path.rstrip("/") + "/"
        team_content = files.get(prefix + "team.yaml")
        digest = _team_digest(team_content)
        area_records.append(item.model_copy(update={"path": f"areas/{item.id}", "team_sha256": digest}))
        area_digests.append({"id": item.id, "team_sha256": digest})
        for source_path, content in sorted(files.items()):
            if not source_path.startswith(prefix):
                continue
            relative = source_path[len(prefix):]
            destination = f"areas/{item.id}/{relative}"
            allowed = _area_file_allowed(relative)
            code_file = relative in CODE_FILES
            suffix_allowed = Path(relative).suffix.lower() in CODE_FREE_SUFFIXES
            if destination in omit_paths:
                omitted.append({"code": "export_user_omitted", "path": destination, "workflow": "", "step": ""})
            elif not excluded_path(relative) and ((allowed and suffix_allowed) or (include_code and code_file)):
                selected[destination] = content
            else:
                omitted.append({"code": "export_file_not_portable", "path": destination, "workflow": "", "step": ""})

    office_fields = {"warnings"}
    if area is not None:
        office_fields.update({"orchestrator", "relations"})
    office_document = definition.model_copy(update={"areas": area_records}).model_dump(
        mode="json", by_alias=True, exclude=office_fields, exclude_none=True)
    office_document["areas"] = [
        {**entry, "path": f"areas/{item.id}", "team_sha256": digest}
        for entry, item, digest in zip(office_document["areas"], exported_areas,
                                      (row["team_sha256"] for row in area_digests))
    ]
    office_bytes = yaml.safe_dump(office_document, sort_keys=False, allow_unicode=True).encode("utf-8")
    archive_files = {"office.yaml": office_bytes, **selected}

    warnings = sorted(omitted, key=lambda item: item["path"])
    for item in exported_areas:
        prefix = item.path.rstrip("/") + "/"
        models_path = prefix + "catalog/models.yaml"
        if models_path not in files:
            continue
        model_catalog = _load_yaml(files[models_path], f"areas/{item.id}/catalog/models.yaml")
        models = model_catalog.get("models") if isinstance(model_catalog, dict) else None
        if not isinstance(models, dict):
            continue
        for source_path, content in sorted(files.items()):
            relative = source_path[len(prefix):] if source_path.startswith(prefix) else ""
            if not relative.startswith("workflows/") or relative.count("/") != 1 or not relative.endswith(".yaml"):
                continue
            workflow = _load_yaml(content, f"areas/{item.id}/{relative}")
            steps = workflow.get("steps") if isinstance(workflow, dict) else None
            if not isinstance(steps, list):
                continue
            for step in steps:
                model_id = step.get("preferred_model") if isinstance(step, dict) else None
                step_id = step.get("id") if isinstance(step, dict) else None
                workflow_id = workflow.get("id") if isinstance(workflow, dict) else None
                if isinstance(model_id, str) and model_id in models and isinstance(step_id, str):
                    warnings.append({"code": "export_model_not_portable",
                                     "path": f"areas/{item.id}/{source_path[len(prefix):]}",
                                     "workflow": workflow_id if isinstance(workflow_id, str) else
                                     PurePosixPath(source_path).stem, "step": step_id})
    warnings.sort(key=lambda item: (item["code"] != "export_model_not_portable", item["path"],
                                    item.get("workflow", ""), item.get("step", "")))

    manifest: dict[str, Any] = {
        "schema": EXPORT_SCHEMA,
        "kind": "area" if area is not None else "office",
        "office": {"id": definition.id, "name": definition.name},
        "exported_at": utcnow(),
        "files": [{"path": path, "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
                  for path, content in sorted(archive_files.items())],
        "areas": area_digests,
        "warnings": warnings,
    }
    manifest_bytes = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
    found = _text_findings({**archive_files, MANIFEST_FILE: manifest_bytes})
    if found:
        raise ServiceError(INVALID_REQUEST, "The export contains a secret", status=422,
                           details={"reason": "export_secret_found", "found": found})

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in sorted(archive_files.items()):
            archive.writestr(path, content)
        archive.writestr(MANIFEST_FILE, manifest_bytes)
    return buffer.getvalue(), manifest


def check_manifest(root: Path) -> None:
    """Verify an optional export manifest against all extracted files except the manifest itself."""
    manifest_path = root / MANIFEST_FILE
    if not manifest_path.exists():
        return

    def mismatch(path: str = MANIFEST_FILE) -> ServiceError:
        return ServiceError(INVALID_REQUEST, "The office export manifest does not match its files", status=422,
                            details={"reason": "office_manifest_mismatch", "path": path[:200]})

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise mismatch() from None
    if not isinstance(manifest, dict) or manifest.get("schema") != EXPORT_SCHEMA:
        raise mismatch()
    rows = manifest.get("files")
    if not isinstance(rows, list):
        raise mismatch()

    expected: dict[str, tuple[str, int]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise mismatch()
        path, digest, size = row.get("path"), row.get("sha256"), row.get("size")
        if (not isinstance(path, str) or not path or "\\" in path or PurePosixPath(path).is_absolute()
                or any(part in ("", ".", "..") for part in path.split("/")) or path == MANIFEST_FILE
                or not isinstance(digest, str) or not _SHA256.fullmatch(digest)
                or isinstance(size, bool) or not isinstance(size, int) or size < 0 or path in expected):
            raise mismatch(path if isinstance(path, str) else MANIFEST_FILE)
        expected[path] = (digest, size)

    actual_paths = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
                    and path != manifest_path}
    for path, (digest, size) in expected.items():
        candidate = root.joinpath(*path.split("/"))
        if path not in actual_paths or not candidate.is_file():
            raise mismatch(path)
        content = candidate.read_bytes()
        if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
            raise mismatch(path)
    extra = sorted(actual_paths - set(expected))
    if extra:
        raise mismatch(extra[0])
