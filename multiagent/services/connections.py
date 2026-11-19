# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Read-only, cached connection facts for model providers and executor apps."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..executors.antigravity import LAUNCH_FLOOR_PERCENT, NOTICE_ID, PAUSE_FLOOR_PERCENT
from ..setup.detect import detect
from .errors import NOT_FOUND, ServiceError

IDS = ("gemini", "openai", "ollama", "claude_cli", "codex_cli", "copilot_cli", "antigravity_cli")
APPS = {"claude_cli": "claude", "codex_cli": "codex", "copilot_cli": "copilot", "antigravity_cli": "agy"}
KEYS = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}
KEY_FILE = ".local/.env"


def _tls_failure(error: Any) -> bool:
    return "CERTIFICATE_VERIFY_FAILED" in str(error).upper() or "CERTIFICATE_VERIFY_FAILED" in repr(error).upper()


class ConnectionServices:
    """Inspect fixed, non-generating signals; never return credentials or raw command output."""

    def __init__(self, services, *, detector: Callable[..., dict] = detect,
                 clock: Callable[[], datetime] | None = None):
        self._services = services
        self.detector = detector
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._last: dict[str, dict[str, Any]] = {}
        self._batch_detection: dict[str, Any] | None = None

    def list(self) -> dict[str, Any]:
        if any(identifier not in self._last for identifier in APPS):
            try:
                self._batch_detection = self.detector(cwd=Path.cwd(), context="packaged_process",
                                                      include_antigravity=True)
            except Exception:
                self._batch_detection = {}
        try:
            for identifier in IDS:
                if identifier not in self._last:
                    self.check(identifier)
        finally:
            self._batch_detection = None
        return {"connections": [dict(self._last[identifier]) for identifier in IDS]}

    def check(self, identifier: str) -> dict[str, Any]:
        if identifier not in IDS:
            raise ServiceError(NOT_FOUND, "Connection not found", status=404, details={"id": identifier})
        now = self.clock().astimezone(timezone.utc).isoformat()
        row: dict[str, Any] = {"id": identifier, "kind": "executor" if identifier in APPS else "model",
                               "installed": None, "version": None, "session": "not_applicable",
                               "key_configured": None, "key_setting": None, "key_file": None,
                               "reachable": None, "enabled": True, "quota": None,
                               "available": False, "reason": None, "missing_step": None,
                               "checked_at": now}
        if identifier in APPS:
            self._executor(row)
        else:
            self._model(row)
        row["available"] = row["reason"] is None
        self._last[identifier] = row
        return dict(row)

    def _executor(self, row: dict[str, Any]) -> None:
        identifier = row["id"]
        app = APPS[identifier]
        settings = self._services.settings
        try:
            observed = self._batch_detection if self._batch_detection is not None else self.detector(
                cwd=Path.cwd(), context="packaged_process", include_antigravity=True)
            apps = observed["contexts"]["packaged_process"].get("apps", [])
            fact = next((item for item in apps if item.get("app") == app), {})
        except Exception:
            fact = {}
        row["installed"] = fact.get("detection") == "detected" if fact else None
        row["version"] = fact.get("version")
        row["session"] = (fact.get("session") or {}).get("state", "unknown")
        if app == "copilot":
            row["session"] = "unknown"
            row["session_note"] = "Cannot check the Copilot session without spending model quota."
        elif app == "agy":
            row["session"] = "unknown"
            row["session_note"] = "The /usage quota probe is the only safe session signal."
        try:
            state = next((item for item in self._services.executors.status()["executors"]
                          if item["executor_id"] == identifier), None)
        except Exception:
            state = None
        row["enabled"] = bool(state and state["enabled"])
        if app == "agy":
            row["notice_id"] = NOTICE_ID
            row["notice_accepted"] = settings.antigravity_notice_accepted == NOTICE_ID
        if row["installed"] is False:
            row.update(reason="not_installed", missing_step="install")
        elif not row["enabled"] or app == "agy" and not row["notice_accepted"]:
            row.update(reason="notice_not_accepted" if app == "agy" and not row["notice_accepted"] else "disabled",
                       missing_step="enable")
        elif row["session"] == "signed_out":
            row.update(reason="session_missing", missing_step="session")
        elif row["installed"] is None or row["session"] == "unknown" and app not in {"copilot", "agy"}:
            row["reason"] = "status_unknown"
        elif app == "copilot":
            row["reason"] = "session_unverifiable"
        if state and state.get("pause_reason"):
            row["reason"] = "quota_paused"
            row["paused_until"] = state.get("paused_until")
        self._quota(row, app)

    def _quota(self, row: dict[str, Any], app: str) -> None:
        try:
            entry = next((item for item in self._services.usage.plans()["apps"]
                          if item["app"] == ("antigravity" if app == "agy" else app)), None)
        except Exception:
            entry = None
        if not entry:
            return
        windows = [{"window": item.get("window"), "group": item.get("group"),
                    "used_percent": item.get("used_percent"), "resets_at": item.get("resets_at")}
                   for item in entry.get("windows", [])]
        row["quota"] = {"status": entry.get("status"), "windows": windows}
        if app == "agy":
            if windows and entry.get("status") in {"ok", "partial"}:
                row["session"] = "signed_in"
            elif row["reason"] is None:
                row["reason"] = "usage_unavailable"
            row["quota"]["launch_floor_remaining_percent"] = LAUNCH_FLOOR_PERCENT
            row["quota"]["pause_floor_remaining_percent"] = PAUSE_FLOOR_PERCENT
            remaining = [100 - item["used_percent"] for item in windows if isinstance(item["used_percent"], (int, float))]
            if row["reason"] is None and remaining and min(remaining) < LAUNCH_FLOOR_PERCENT:
                row["reason"] = "quota_below_pause_floor" if min(remaining) < PAUSE_FLOOR_PERCENT else "quota_below_launch_floor"

    def _model(self, row: dict[str, Any]) -> None:
        identifier = row["id"]
        settings = self._services.settings
        if identifier in KEYS:
            row["key_setting"] = KEYS[identifier]
            row["key_file"] = KEY_FILE
            row["key_configured"] = bool(getattr(settings, f"{identifier}_api_key"))
            row["enabled"] = identifier != "openai" or settings.openai_enabled
            if not row["key_configured"]:
                row.update(reason="key_missing", missing_step="key")
            elif not row["enabled"]:
                row.update(reason="disabled", missing_step="enable")
            elif identifier == "gemini":
                row["reason"] = "reachability_unknown"
            return
        try:
            adapter = self._services.system.adapters["ollama"]
            from ..adapters.ollama import OllamaAdapter
            if isinstance(adapter, OllamaAdapter):
                import httpx
                with httpx.Client(base_url=settings.ollama_base_url, timeout=5) as client:
                    health = OllamaAdapter(settings, client=client).health()
            else:
                health = adapter.health()
            row["reachable"] = bool(health.get("available"))
            row["version"] = health.get("version")
            if not row["reachable"]:
                row["reason"] = "tls_certificate" if _tls_failure(health.get("error")) else "unreachable"
                row["diagnosis"] = ("The TLS certificate could not be verified; check the local trust chain."
                                    if row["reason"] == "tls_certificate" else "Ollama did not answer its health probe.")
        except Exception as exc:
            row["reason"] = "tls_certificate" if _tls_failure(exc) else "unreachable"
            row["diagnosis"] = ("The TLS certificate could not be verified; check the local trust chain."
                                if row["reason"] == "tls_certificate" else "Ollama did not answer its health probe.")
