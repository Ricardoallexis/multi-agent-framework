# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Fingerprint registered bundles and gate imports on a cached, approved scan."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .. import bundle_scan
from ..external_output import canonical_json, sha256
from .errors import ServiceError

if TYPE_CHECKING:
    from . import ApplicationServices


class BundleScanServices:
    """One scan worker per facade. Reads of status never request approval or run a scan."""

    def __init__(self, services: ApplicationServices):
        self._services = services
        self._lock = threading.RLock()
        self._worker: threading.Thread | None = None
        self._running: tuple[str, str] | None = None
        self.verifier = None
        self.runner = None
        self.env = None
        # "off" and "not_installed" stay in memory only: once SkillSpector is installed or bundle_scan
        # is turned on, the next start scans the bundle instead of trusting a stale file.
        self._unscanned: dict[tuple[str, str], dict[str, Any]] = {}

    @staticmethod
    def fingerprint(folder: Path) -> str:
        digest = hashlib.sha256()
        # __pycache__ is not bundle content: the loader never reads or writes it (bundles._load_contracts).
        files = (p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.relative_to(folder).parts)
        for path in sorted(files, key=lambda p: p.relative_to(folder).as_posix()):
            relative = path.relative_to(folder).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(path.stat().st_size.to_bytes(8, "big"))
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()

    def _external(self, folder: Path) -> bool:
        # An installed copy is always scanned, even when data_dir sits inside the checkout (PAQ-T01, L23). The
        # ``shipped`` entries point at the product's own folder, not under installed/, so they stay exempt.
        if folder.is_relative_to((self._services.settings.data_dir / "installed").resolve()):
            return True
        return not folder.is_relative_to(Path(__file__).resolve().parents[2])

    def _cache_path(self, name: str, fingerprint: str) -> Path:
        return self._services.settings.local_dir / "bundle-scans" / f"{name}-{fingerprint}.json"

    def _read_cache(self, name: str, fingerprint: str) -> dict[str, Any] | None:
        try:
            data = json.loads(self._cache_path(name, fingerprint).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("fingerprint") != fingerprint or not isinstance(data.get("scan"), dict):
            return None
        return None if data["scan"].get("state") in {"off", "not_installed"} else data

    def _cached(self, name: str, fingerprint: str) -> dict[str, Any] | None:
        return self._read_cache(name, fingerprint) or self._unscanned.get((name, fingerprint))

    def _write_cache(self, name: str, fingerprint: str, data: dict[str, Any]) -> None:
        path = self._cache_path(name, fingerprint)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _manifests(folder: Path) -> list[str]:
        return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*")
                      if p.is_file() and (p.name.startswith("requirements") and p.suffix == ".txt"
                                          or p.name in {"pyproject.toml", "package.json"}))[:12]

    def _approval(self, kind: str, name: str, fingerprint: str, *, folder: Path) -> dict[str, Any]:
        destination = {"bundle": name, "kind": kind}
        approvals = self._services.user_approvals
        # A consumed approval belongs to a scan or load already started; an interrupted scan asks again.
        existing = [item for item in approvals._all().values()
                    if item["kind"] == kind and item["sha256"] == fingerprint and item["destination"] == destination
                    and item["state"] != "consumed"
                    and not (kind == "bundle_load" and item["state"] == "approved"
                             and item.get("risk_ack") is not True)]
        if existing:
            return existing[-1]
        manifests = self._manifests(folder) if kind == "osv_query" else []
        location = (f"api.osv.dev receives package names and versions from: "
                    f"{', '.join(manifests) or 'no manifests found'}"[:300]
                    if kind == "osv_query" else {"bundle": name, "fingerprint": fingerprint})
        return approvals.request(kind=kind, destination=destination, location=location, sha256=fingerprint)

    def _worker_run(self, name: str, folder: Path, fingerprint: str) -> None:
        try:
            try:
                result = bundle_scan.scan(folder, osv_approved=True, settings=self._services.settings,
                                          env=self.env, verifier=self.verifier, runner=self.runner)
            except Exception as exc:  # a failed worker must leave a reviewable result
                result = bundle_scan.ScanResult(bundle_scan.NOT_ANALYZED, reason=f"scan_failed: {type(exc).__name__}")
            with self._lock:
                self._write_cache(name, fingerprint, {"fingerprint": fingerprint, "scan": result.as_dict(),
                                                       "load_approved": False})
        finally:
            with self._lock:
                self._running = None

    def status(self, name: str) -> dict[str, Any]:
        folder = self._services.bundle_path(name)
        external = self._external(folder)
        if not external:
            return {"bundle": name, "external": False, "state": "exempt", "verdict": None,
                    "findings": [], "coverage": {}, "fingerprint": None, "reason": None,
                    "warning": None, "approval": None, "tool_version": None, "load_approved": False,
                    "recommendation": None, "risk_ack_required": False}
        fingerprint = self.fingerprint(folder)
        with self._lock:
            cached = self._cached(name, fingerprint)
            running = self._running == (name, fingerprint)
        scan = cached["scan"] if cached else {}
        state = "running" if running else scan.get("state", "unscanned")
        warning = "Not scanned: SkillSpector is not installed" if state in {"off", "not_installed"} else None
        load_approved = (cached or {}).get("load_approved") is True and (cached or {}).get("risk_ack") is True
        approval = None
        if state == "unscanned":
            kind = "osv_query"
        elif state not in {"running", "safe", "caution", "off", "not_installed"} and not load_approved:
            kind = "bundle_load"
        else:
            kind = None
        if kind:
            destination = {"bundle": name, "kind": kind}
            matches = [item for item in self._services.user_approvals._all().values()
                       if item["kind"] == kind and item["sha256"] == fingerprint and item["destination"] == destination
                       and item["state"] != "consumed"
                       and not (kind == "bundle_load" and item["state"] == "approved"
                                and item.get("risk_ack") is not True)]
            approval = matches[-1] if matches else None
        if state == "unscanned" and approval is not None:
            state = "pending" if approval["state"] == "pending" else "unscanned"
        return {"bundle": name, "external": True, "state": state,
                "verdict": state if state in {"safe", "caution", "do_not_install"} else None,
                "findings": scan.get("findings", []), "coverage": scan.get("coverage", {}),
                "fingerprint": fingerprint, "reason": scan.get("reason"), "warning": warning,
                "tool_version": scan.get("tool_version"), "load_approved": load_approved,
                "recommendation": "do_not_install" if state == "do_not_install" else None,
                "risk_ack_required": kind == "bundle_load",
                "approval": {"id": approval["id"], "state": approval["state"],
                             "location": "/gui/"} if approval else None}

    def _office_registry_incomplete(self, name: str,
                                    registered: dict[str, dict[str, Any]]) -> bool:
        package = registered.get(name)
        if not isinstance(package, dict) or package.get("source") != "office":
            return False
        office_id = package.get("office")
        if not isinstance(office_id, str):
            return True
        office = self._services.offices._read().get(office_id)
        if not isinstance(office, dict) or not isinstance(office.get("fingerprint"), str):
            return True
        definition = office.get("definition")
        areas = definition.get("areas") if isinstance(definition, dict) else None
        if not isinstance(areas, list):
            return True
        expected = {f"{office_id}__{area['id']}" for area in areas
                    if isinstance(area, dict) and isinstance(area.get("id"), str)}
        actual = {bundle for bundle, entry in registered.items()
                  if entry.get("source") == "office" and entry.get("office") == office_id}
        if expected != actual or name not in expected:
            return True
        fingerprints = {bundle: registered[bundle].get("fingerprint") for bundle in sorted(expected)}
        return sha256(canonical_json({"definition": definition, "areas": fingerprints})) != office["fingerprint"]

    def _office_scan_context(self, name: str, registered: dict[str, dict[str, Any]]
                             ) -> tuple[str, str, dict[str, Path]] | None:
        package = registered.get(name)
        if not isinstance(package, dict) or package.get("source") != "office":
            return None
        office_id = package.get("office")
        if not isinstance(office_id, str) or self._office_registry_incomplete(name, registered):
            return None
        office = self._services.offices._read().get(office_id)
        if not isinstance(office, dict):
            return None
        definition = office.get("definition")
        areas = definition.get("areas") if isinstance(definition, dict) else None
        if not isinstance(areas, list):
            return None
        folders: dict[str, Path] = {}
        fingerprints: dict[str, str] = {}
        for area in areas:
            if not isinstance(area, dict) or not isinstance(area.get("id"), str):
                continue
            area_id = area["id"]
            bundle = f"{office_id}__{area_id}"
            entry = registered.get(bundle)
            if (isinstance(entry, dict) and entry.get("source") == "office"
                    and entry.get("office") == office_id):
                folder = self._services.bundle_path(bundle)
                if not folder.is_dir():
                    return None
                folders[area_id] = folder
                fingerprints[bundle] = self.fingerprint(folder)
        if not folders or name not in {f"{office_id}__{area_id}" for area_id in folders}:
            return None
        office_fingerprint = sha256(canonical_json({"definition": definition, "areas": fingerprints}))
        return office_id, office_fingerprint, folders

    def require_load(self, name: str) -> dict[str, Any]:
        folder = self._services.bundle_path(name)
        if not self._external(folder):
            return self.status(name)
        registered = self._services.installed._read()
        office_context = self._office_scan_context(name, registered)
        if office_context is not None:
            office_id, office_fingerprint, folders = office_context
            office_scan = self.scan_office(office_id, office_fingerprint, folders)
            if office_scan["state"] == "pending":
                raise ServiceError("bundle_scan_pending", "Approve the office OSV query in the GUI", status=409,
                                   details={"approval_id": office_scan.get("approval_id"), "location": "/gui/",
                                            "office": office_id})
            if office_scan["state"] in {"running", "unscanned"}:
                raise ServiceError("bundle_scan_running", "The office scan is not finished", status=409,
                                   details={"office": office_id})
        fingerprint = self.fingerprint(folder)
        with self._lock:
            cached = self._cached(name, fingerprint)
            if cached is None:
                settings = self._services.settings
                if settings.bundle_scan == "off":
                    detection = bundle_scan.ScanResult(bundle_scan.OFF, reason="bundle_scan_off")
                elif settings.mock_mode and not settings.skillspector_python:
                    # Mock never inspects the user's installed tool or starts an OSV query.
                    detection = bundle_scan.ScanResult(bundle_scan.NOT_INSTALLED, reason="mock_mode")
                else:
                    detection = bundle_scan.detect(settings, env=self.env, verifier=self.verifier)
                if detection.state in {"off", "not_installed"}:
                    self._unscanned[(name, fingerprint)] = {"fingerprint": fingerprint,
                                                            "scan": {"state": detection.state, "findings": [],
                                                                     "coverage": {}, "reason": detection.reason},
                                                            "load_approved": False}
                    return self.status(name)
                if detection.state == "unsigned_interpreter":
                    cached = {"fingerprint": fingerprint,
                              "scan": bundle_scan.ScanResult(bundle_scan.NOT_ANALYZED,
                                                              reason=detection.reason).as_dict(),
                              "load_approved": False}
                    self._write_cache(name, fingerprint, cached)
                else:
                    if self._running is not None and self._running != (name, fingerprint):
                        raise ServiceError("bundle_scan_running", "Another bundle scan is running", status=409,
                                           details={"bundle": name})
                    if self._running == (name, fingerprint):
                        raise ServiceError("bundle_scan_running", "Bundle scan is running", status=409,
                                           details={"bundle": name})
                    approval = self._approval("osv_query", name, fingerprint, folder=folder)
                    if approval["state"] == "approved" and self._services.user_approvals.consume(
                            approval["id"], sha256=fingerprint, destination={"bundle": name, "kind": "osv_query"}):
                        if self._running is None:
                            self._running = (name, fingerprint)
                            self._worker = threading.Thread(target=self._worker_run,
                                                            args=(name, folder, fingerprint), name="bundle-scan")
                            self._worker.start()
                    elif approval["state"] == "rejected":
                        cached = {"fingerprint": fingerprint,
                                  "scan": bundle_scan.ScanResult(bundle_scan.NOT_ANALYZED,
                                                                  reason="osv_rejected").as_dict(),
                                  "load_approved": False}
                        self._write_cache(name, fingerprint, cached)
                    else:
                        raise ServiceError("bundle_scan_pending", "Approve the OSV query in the GUI", status=409,
                                           details={"approval_id": approval["id"], "location": "/gui/"})
            if self._running == (name, fingerprint) or cached is None:
                raise ServiceError("bundle_scan_running", "Bundle scan is running", status=409,
                                   details={"bundle": name})
            scan = cached["scan"]
            if (scan["state"] in {"safe", "caution", "off", "not_installed"}
                    or (cached.get("load_approved") is True and cached.get("risk_ack") is True)):
                return self.status(name)
            approval = self._approval("bundle_load", name, fingerprint, folder=folder)
            if approval["state"] == "approved" and approval.get("risk_ack") is True and self._services.user_approvals.consume(
                    approval["id"], sha256=fingerprint, destination={"bundle": name, "kind": "bundle_load"}):
                cached["load_approved"] = True
                cached["risk_ack"] = True
                self._write_cache(name, fingerprint, cached)
                return self.status(name)
            raise ServiceError("bundle_scan_blocked", "Approve loading this bundle in the GUI", status=409,
                               details={"approval_id": approval["id"], "location": "/gui/",
                                        "verdict": scan["state"], "findings": scan.get("findings", []),
                                        "coverage": scan.get("coverage", {}), "risk_ack_required": True})

    def _office_scan_result(self, office_id: str, folders: dict[str, Path], fingerprints: dict[str, str], *,
                            approval_id: str | None = None, pending: bool = False,
                            running: bool = False) -> dict[str, Any]:
        areas: dict[str, dict[str, Any]] = {}
        states = []
        for area_id, folder in folders.items():
            bundle = f"{office_id}__{area_id}"
            cached = self._cached(bundle, fingerprints[area_id])
            scan = (cached or {}).get("scan") or {}
            state = "pending" if pending and cached is None else "running" if running and cached is None else scan.get(
                "state", "unscanned")
            areas[area_id] = {"state": state, "findings": scan.get("findings", []),
                              "coverage": scan.get("coverage", {})}
            states.append(state)
        if pending:
            state = "pending"
        elif running:
            state = "running"
        else:
            priority = ("do_not_install", "not_analyzed", "unscanned", "caution", "safe", "off", "not_installed")
            state = next((candidate for candidate in priority if candidate in states), "safe")
        return {"state": state, "approval_id": approval_id, "areas": areas}

    def _office_scan_worker(self, office_id: str, office_fingerprint: str, folders: dict[str, Path],
                            fingerprints: dict[str, str]) -> None:
        try:
            for area_id, folder in folders.items():
                bundle = f"{office_id}__{area_id}"
                fingerprint = fingerprints[area_id]
                with self._lock:
                    if self._cached(bundle, fingerprint) is not None:
                        continue
                try:
                    result = bundle_scan.scan(folder, osv_approved=True, settings=self._services.settings,
                                              env=self.env, verifier=self.verifier, runner=self.runner)
                except Exception as exc:  # a failed worker must leave a reviewable result
                    result = bundle_scan.ScanResult(bundle_scan.NOT_ANALYZED,
                                                    reason=f"scan_failed: {type(exc).__name__}")
                with self._lock:
                    self._write_cache(bundle, fingerprint, {"fingerprint": fingerprint, "scan": result.as_dict(),
                                                            "load_approved": False})
        finally:
            with self._lock:
                if self._running == (f"office:{office_id}", office_fingerprint):
                    self._running = None

    def _office_approval_id(self, office_id: str, fingerprint: str) -> str | None:
        destination = {"office": office_id, "kind": "osv_query"}
        matches = [item for item in self._services.user_approvals._all().values()
                   if item["kind"] == "osv_query" and item["sha256"] == fingerprint
                   and item["destination"] == destination]
        return matches[-1]["id"] if matches else None

    def scan_office(self, office_id: str, fingerprint: str, folders: dict[str, Path]) -> dict[str, Any]:
        """Request one OSV approval for an office, then scan its areas in order and cache each area by its fingerprint."""
        folders = dict(folders)
        fingerprints = {area_id: self.fingerprint(folder) for area_id, folder in folders.items()}
        key = (f"office:{office_id}", fingerprint)
        with self._lock:
            cached_areas = {area_id: self._cached(f"{office_id}__{area_id}", area_fp)
                            for area_id, area_fp in fingerprints.items()}
            if all(item is not None for item in cached_areas.values()):
                return self._office_scan_result(office_id, folders, fingerprints,
                                                approval_id=self._office_approval_id(office_id, fingerprint))
            if self._running == key:
                return self._office_scan_result(office_id, folders, fingerprints,
                                                approval_id=self._office_approval_id(office_id, fingerprint),
                                                running=True)

            settings = self._services.settings
            if settings.bundle_scan == "off":
                detection = bundle_scan.ScanResult(bundle_scan.OFF, reason="bundle_scan_off")
            elif settings.mock_mode and not settings.skillspector_python:
                detection = bundle_scan.ScanResult(bundle_scan.NOT_INSTALLED, reason="mock_mode")
            else:
                detection = bundle_scan.detect(settings, env=self.env, verifier=self.verifier)
            if detection.state in {"off", "not_installed"}:
                for area_id, area_fp in fingerprints.items():
                    if cached_areas[area_id] is None:
                        self._unscanned[(f"{office_id}__{area_id}", area_fp)] = {
                            "fingerprint": area_fp,
                            "scan": {"state": detection.state, "findings": [], "coverage": {},
                                     "reason": detection.reason},
                            "load_approved": False,
                        }
                return self._office_scan_result(office_id, folders, fingerprints)
            if detection.state == "unsigned_interpreter":
                for area_id, area_fp in fingerprints.items():
                    if cached_areas[area_id] is None:
                        self._write_cache(f"{office_id}__{area_id}", area_fp, {
                            "fingerprint": area_fp,
                            "scan": bundle_scan.ScanResult(bundle_scan.NOT_ANALYZED,
                                                           reason=detection.reason).as_dict(),
                            "load_approved": False,
                        })
                return self._office_scan_result(office_id, folders, fingerprints)

            destination = {"office": office_id, "kind": "osv_query"}
            approvals = self._services.user_approvals
            matches = [item for item in approvals._all().values()
                       if item["kind"] == "osv_query" and item["sha256"] == fingerprint
                       and item["destination"] == destination and item["state"] != "consumed"]
            if matches:
                approval = matches[-1]
            else:
                manifests = sorted({f"{area_id}/{manifest}" for area_id, folder in folders.items()
                                    for manifest in self._manifests(folder)})
                location = ("api.osv.dev receives package names and versions from: "
                            f"{', '.join(manifests) or 'no manifests found'}")[:300]
                approval = approvals.request(kind="osv_query", destination=destination, location=location,
                                             sha256=fingerprint)
            if approval["state"] == "rejected":
                for area_id, area_fp in fingerprints.items():
                    if cached_areas[area_id] is None:
                        self._write_cache(f"{office_id}__{area_id}", area_fp, {
                            "fingerprint": area_fp,
                            "scan": bundle_scan.ScanResult(bundle_scan.NOT_ANALYZED,
                                                           reason="osv_rejected").as_dict(),
                            "load_approved": False,
                        })
                return self._office_scan_result(office_id, folders, fingerprints, approval_id=approval["id"])
            if approval["state"] != "approved":
                return self._office_scan_result(office_id, folders, fingerprints, approval_id=approval["id"],
                                                pending=True)
            if self._running is not None and self._running != key:
                raise ServiceError("bundle_scan_running", "Another bundle scan is running", status=409,
                                   details={"bundle": office_id})
            if not approvals.consume(approval["id"], sha256=fingerprint, destination=destination):
                return self._office_scan_result(office_id, folders, fingerprints, approval_id=approval["id"],
                                                running=self._running == key)
            self._running = key
            self._worker = threading.Thread(target=self._office_scan_worker,
                                            args=(office_id, fingerprint, folders, fingerprints),
                                            name="office-bundle-scan")
            self._worker.start()
            return self._office_scan_result(office_id, folders, fingerprints, approval_id=approval["id"], running=True)

    def close(self) -> None:
        with self._lock:
            worker = self._worker
        if worker is not None:
            worker.join()
