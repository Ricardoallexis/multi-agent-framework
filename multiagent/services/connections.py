# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""Read-only, cached connection facts for model providers and executor apps."""
from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..adapters.tls import effective_tls_trust, is_tls_failure, provider_ssl_context
from ..executors.antigravity import LAUNCH_FLOOR_PERCENT, NOTICE_ID, PAUSE_FLOOR_PERCENT
from ..executors.acp_probe import probe_codex_acp, resolve_codex_acp
from ..routing.executor_capabilities import CapabilityEvidence, SupportState, validate_executor_descriptor
from ..setup.detect import detect
from .errors import NOT_FOUND, ServiceError
from .limits import LIMIT_REASONS, limit_notice

IDS = ("gemini", "openai", "ollama", "claude_cli", "codex_cli", "codex_acp", "copilot_cli", "antigravity_cli")
APPS = {"claude_cli": "claude", "codex_cli": "codex", "copilot_cli": "copilot", "antigravity_cli": "agy"}
ACP_CONNECTION_ID = "codex_acp"
ACP_EXECUTOR_ID = "codex_cli"
# How a person installs the Codex ACP adapter. It is shown as read-only data (row ``install``); the product never runs it.
# Revalidated by the orchestrator on 2026-11-26 against registry.npmjs.org: 2.12.0 exists and is Apache-2.0;
# 2.12.1, published the same day, is not adopted yet.
CODEX_ACP_INSTALL = {
    "package": "@agentclientprotocol/codex-acp",
    "version": "2.12.0",
    "license": "Apache-2.0",
    "command": "npm install -g @agentclientprotocol/codex-acp@2.12.0",
    "source": "https://www.npmjs.com/package/@agentclientprotocol/codex-acp",
}
KEYS = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}
KEY_FILE = ".local/.env"
PROVIDERS = ("gemini", "openai", "ollama")     # the rows with a model provider: they share the certificates setting
TLS_EVENT = "provider.tls_trust_changed"
TLS_SETTING = "provider_tls_trust"
TLS_FIX_LABELS = {"windows": "Usar los certificados de Windows", "other": "Usar los certificados del sistema"}
TLS_REVERT_LABEL = "Volver a los certificados de siempre"


def _is_windows() -> bool:
    return sys.platform == "win32"


def _tls_failure(error: Any) -> bool:
    return is_tls_failure(error)


class ConnectionServices:
    """Inspect fixed, non-generating signals; never return credentials or raw command output."""

    def __init__(self, services, *, detector: Callable[..., dict] = detect,
                 clock: Callable[[], datetime] | None = None,
                 acp_resolver: Callable[[Path], str | None] = resolve_codex_acp,
                 acp_probe: Callable[..., dict[str, Any]] = probe_codex_acp):
        self._services = services
        self.detector = detector
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._acp_resolver = acp_resolver
        self._acp_probe = acp_probe
        self._last: dict[str, dict[str, Any]] = {}
        self._batch_detection: dict[str, Any] | None = None
        self._probed: dict[str, str] = {}      # the outcome of the last "Test connection" of each provider
        self._acp_probe_result: dict[str, Any] | None = None
        self._acp_probe_path: str | None = None

    def list(self) -> dict[str, Any]:
        if any(identifier not in self._last for identifier in APPS):
            try:
                self._batch_detection = self.detector(cwd=Path.cwd(), context="packaged_process",
                                                      include_antigravity=True, parallel_apps=True,
                                                      include_requirements=False)
            except Exception:
                self._batch_detection = {}
        try:
            for identifier in IDS:
                if identifier in {"gemini", ACP_CONNECTION_ID} or identifier not in self._last:
                    self.check(identifier)
        finally:
            self._batch_detection = None
        # A complete snapshot gives every limited row the same eligible alternatives.
        for identifier in IDS:
            row = self._last[identifier]
            if row.get("limit") or row.get("reason") in LIMIT_REASONS or any(
                window.get("used_percent") is not None and window["used_percent"] >= 100
                for window in (row.get("quota") or {}).get("windows") or []
            ):
                row["limit"] = limit_notice(self._services, identifier, rows=self._last.values())
        observed_at = max(
            (self._last[identifier].get("detection_observed_at") for identifier in APPS
             if self._last[identifier].get("detection_observed_at")),
            default=None,
        )
        return {"connections": [dict(self._last[identifier]) for identifier in IDS],
                "detection_cache": {"scope": "service_memory", "observed_at": observed_at}}

    def check(self, identifier: str) -> dict[str, Any]:
        if identifier not in IDS:
            raise ServiceError(NOT_FOUND, "Connection not found", status=404, details={"id": identifier})
        now = self.clock().astimezone(timezone.utc).isoformat()
        row: dict[str, Any] = {"id": identifier,
                               "kind": "executor" if identifier in APPS or identifier == ACP_CONNECTION_ID else "model",
                               "installed": None, "version": None, "session": "not_applicable",
                               "key_configured": None, "key_setting": None, "key_file": None,
                               "reachable": None, "enabled": True, "quota": None,
                               "available": False, "reason": None, "missing_step": None,
                               "checked_at": now}
        if identifier in APPS or identifier == ACP_CONNECTION_ID:
            self._executor(row)
        else:
            self._model(row)
            self._tls_fields(row)
        row["available"] = row["reason"] is None
        if row.get("limit") or row.get("reason") in LIMIT_REASONS or any(
            window.get("used_percent") is not None and window["used_percent"] >= 100
            for window in (row.get("quota") or {}).get("windows") or []
        ):
            row["limit"] = limit_notice(self._services, identifier, rows=[*self._last.values(), row])
        self._last[identifier] = row
        return dict(row)

    def acp_probe_state(self) -> tuple[str | None, bool, dict[str, Any]]:
        """``(resolved executable, probe is of that executable, the probe evidence that still applies)``.

        Evidence belongs to the executable that was probed; another executable shows none of it. One helper for the
        connection row and for the activation service, so both judge a stale probe the same way.
        """
        current = self._acp_resolver(Path.cwd())
        fresh = self._acp_probe_path == current
        return current, fresh, (dict(self._acp_probe_result or {}) if fresh else {})

    def _executor(self, row: dict[str, Any]) -> None:
        identifier = row["id"]
        if identifier == ACP_CONNECTION_ID:
            current, fresh, probe = self.acp_probe_state()
            installed = current is not None
            compatible = fresh and probe.get("status") == "compatible"
            activations = getattr(self._services, "executor_activations", None)
            activation = activations._read().get(ACP_CONNECTION_ID) if activations is not None else None
            activated = bool(activation and not activation.get("paused"))
            authenticated = getattr(self, "_acp_authenticated", None)
            row.update(installed=installed, compatible=bool(compatible), authenticated=authenticated,
                       activated=activated, selected=False, selected_transport="cli", enabled=activated,
                       protocol_version=probe.get("protocol_version"),
                       version=(probe.get("agent_info") or {}).get("version") if isinstance(probe.get("agent_info"), dict) else None,
                       session="signed_in" if authenticated is True else "not_applicable",
                       install=dict(CODEX_ACP_INSTALL),
                       reason="not_installed" if not installed else "acp_probe_required" if not compatible
                       else "disabled" if not activated else None,
                       missing_step="install" if not installed else "probe" if not compatible
                       else "enable" if not activated else None)
            if fresh and self._acp_probe_result is not None:
                row["acp_probe"] = dict(self._acp_probe_result)
            return
        app = APPS[identifier]
        settings = self._services.settings
        try:
            observed = self._batch_detection if self._batch_detection is not None else self.detector(
                cwd=Path.cwd(), context="packaged_process", include_antigravity=True, app=app)
            apps = observed["contexts"]["packaged_process"].get("apps", [])
            fact = next((item for item in apps if item.get("app") == app), {})
        except Exception:
            fact = {}
        row["executable_found"] = fact.get("executable_found", fact.get("detection") == "detected") if fact else None
        row["installed"] = row["executable_found"]
        row["version"] = fact.get("version")
        row["version_result"] = fact.get("version_result")
        row["detection_context"] = fact.get("context", "packaged_process") if fact else None
        row["windows_package_path"] = bool(fact.get("windows_package_path")) if fact else False
        row["detection_observed_at"] = (fact.get("version_observed_at") or
                                        fact.get("observed_at") or
                                        (observed.get("observed_at") if fact else None) or row["checked_at"])
        row["session"] = (fact.get("session") or {}).get("state", "unknown")
        if app == "copilot":
            row["session"] = "unknown"
            row["session_note"] = "Cannot check the Copilot session without spending model quota."
            row.update(verification="unverified", warning=None, limit=None)
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
        if row["reason"] is None and row["version_result"] in {"probe_failed", "timed_out", "uninterpretable"}:
            row["reason"] = "version_check_failed"
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
        if app in {"claude", "codex"} and row["reason"] is None and any(
            item.get("state") == "limit_reached" or
            isinstance(item.get("used_percent"), (float, int)) and item["used_percent"] >= 100
            for item in entry.get("windows", [])
        ):
            row["reason"] = "quota_paused"
        # CUO-T01: the cache is a session signal and a hard premium-interaction limit.
        if app == "copilot":
            cache = (entry.get("source") or {}).get("kind") == "copilot_cache"
            if cache:
                row["verification"] = "verified"
                row["session"] = "signed_in"
                row.pop("session_note", None)
                if row["reason"] == "session_unverifiable":
                    row["reason"] = None
                monthly = next((item for item in entry.get("windows", []) if item.get("window") == "monthly"), None)
                if monthly and monthly.get("state") == "limit_reached":
                    row["limit"] = {"code": "limit_reached", "resets_at": monthly.get("resets_at"),
                                    "target": "copilot_cli", "suggestions": []}
                    if row["reason"] is None:
                        row["reason"] = "limit_reached"
        if app == "agy":
            if windows and entry.get("status") in {"ok", "partial"}:
                row["session"] = "signed_in"
            elif row["reason"] is None:
                row["reason"] = "usage_unavailable"
            row["quota"]["launch_floor_remaining_percent"] = LAUNCH_FLOOR_PERCENT
            row["quota"]["pause_floor_remaining_percent"] = PAUSE_FLOOR_PERCENT
            measured = [item for item in windows if isinstance(item["used_percent"], (int, float))]
            if row["reason"] is None and measured:
                binding = max(measured, key=lambda item: item["used_percent"])
                remaining = 100 - binding["used_percent"]
                if remaining < LAUNCH_FLOOR_PERCENT:
                    reason = ("quota_below_pause_floor" if remaining < PAUSE_FLOOR_PERCENT
                              else "quota_below_launch_floor")
                    if remaining <= 0:
                        row["reason"] = reason
                    else:
                        row["warning"] = {"reason": reason, "remaining_percent": remaining,
                                          "resets_at": binding["resets_at"]}

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
            elif identifier == "openai" and self._probed.get("openai") == "tls_certificate":
                row["reason"] = "tls_certificate"      # the last "Test connection" failed on the certificate
            elif identifier == "gemini":
                from ..model_checks import gemini_verification
                check = gemini_verification()
                row["verification"] = check["verification"]
                row["verified_at"] = check["verified_at"]
                row["resets_at"] = check["resets_at"]
                if check["verification"] in {"key_invalid", "limit_reached", "no_access", "tls_certificate"}:
                    row["reason"] = check["verification"]
                if check["verification"] == "limit_reached" and not check["resets_at"]:
                    row["limit_note"] = "Reset time is unknown."
            return
        try:
            adapter = self._services.system.adapters["ollama"]
            from ..adapters.ollama import OllamaAdapter
            if isinstance(adapter, OllamaAdapter):
                import httpx
                context = provider_ssl_context(effective_tls_trust(settings))
                extra = {} if context is None else {"verify": context}
                with httpx.Client(base_url=settings.ollama_base_url, timeout=5, **extra) as client:
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

    # -------- ARR3-T07: certificates of the providers --------
    def _tls_fields(self, row: dict[str, Any]) -> None:
        """``tls_trust`` and the one button the card may offer: the system store, or the way back to certifi."""
        trust = effective_tls_trust(self._services.settings)
        row["tls_trust"] = trust
        row["tls_fix"] = None
        row["tls_revert"] = None
        if row["id"] in PROVIDERS and row.get("reason") == "tls_certificate" and trust != "system":
            label = TLS_FIX_LABELS["windows" if _is_windows() else "other"]
            row["tls_fix"] = {"trust": "system", "label": label}
        elif row["id"] in PROVIDERS and trust == "system":
            row["tls_revert"] = {"trust": "certifi", "label": TLS_REVERT_LABEL}

    def probe(self, identifier: str) -> dict[str, Any]:
        """The "Test connection" flow makes one request that generates nothing (the model list or Ollama's health). Only on request."""
        if identifier not in PROVIDERS:
            raise ServiceError(NOT_FOUND, "Connection not found", status=404, details={"id": identifier})
        row = self.check(identifier)
        if row["reason"] in {"key_missing", "disabled"}:
            outcome = "skipped"                       # nothing to try yet: the key or the switch is missing
        elif identifier == "ollama":
            outcome = "tls_certificate" if row["reason"] == "tls_certificate" else "ok" if row["reachable"] else "error"
        else:
            adapter = self._services.system.adapters.get(identifier)
            probe = getattr(adapter, "probe", None)
            try:
                outcome = probe() if callable(probe) else "skipped"
            except Exception:  # noqa: BLE001 - a probe never fails the route
                outcome = "error"
        self._probed[identifier] = outcome
        row = self.check(identifier)
        row["probe"] = outcome
        return row

    def probe_acp(self, identifier: str) -> dict[str, Any]:
        """Explicitly probe the installed Codex ACP adapter with initialize only; never activate or persist it."""
        if identifier != ACP_CONNECTION_ID:
            raise ServiceError(NOT_FOUND, "Connection not found", status=404, details={"id": identifier})
        row = self.check(identifier)
        executable = self._acp_resolver(Path.cwd())
        if executable is None:
            result = {"status": "not_installed"}
        else:
            result = self._acp_probe(executable, cwd=Path.cwd())
        if result.get("status") == "compatible":
            registry = getattr(self._services.executors, "registry", None)
            descriptor = registry.get(ACP_EXECUTOR_ID) if registry is not None else None
            if descriptor is None:
                result["descriptor_error"] = "executor_descriptor_unavailable"
            else:
                observed_at = self.clock().astimezone(timezone.utc).isoformat()
                evidence = CapabilityEvidence(
                    status=SupportState.SUPPORTED, source="codex-acp initialize",
                    provenance="runtime_probe", scope=ACP_EXECUTOR_ID, observed_at=observed_at,
                    values=frozenset({"acp"}))
                overlay = validate_executor_descriptor(replace(
                    descriptor, transports=tuple(dict.fromkeys((*descriptor.transports, "cli", "acp"))),
                    transport_evidence=evidence))
                result["observed_at"] = observed_at
                result["descriptor"] = overlay.to_dict()
        self._acp_probe_result = dict(result)
        self._acp_probe_path = executable
        return self.check(identifier)

    def acp_consent(self, bundle: str) -> dict[str, Any]:
        """Resolve the office area on the server; a client-supplied scope is never trusted."""
        self._services.bundle_path(bundle)
        located = self._services.offices.office_of(bundle)
        if located is None:
            raise ServiceError("invalid_state", "ACP requires a verified office area", status=409,
                               details={"reason": "acp_area_required"})
        scope = "/".join(located)
        setting = self._services.gui_settings.acp_consent(scope)
        return {"bundle": bundle, "scope": scope, "configured": setting is not None,
                "allow_write": bool(setting and setting["allow_write"]), "allow_always": False}

    def set_acp_consent(self, bundle: str, allow_write: bool) -> dict[str, Any]:
        current = self.acp_consent(bundle)
        self._services.gui_settings.set_acp_consent(current["scope"], allow_write=allow_write)
        self._services.runs.acp.close_scope(current["scope"])
        return self.acp_consent(bundle)

    def set_tls_trust(self, identifier: str, trust: str, *, actor: str = "user:gui", channel: str = "gui") -> dict[str, Any]:
        """Write ``provider_tls_trust`` (``system`` or ``certifi``), record the change, drop the cached clients and probe again."""
        if identifier not in PROVIDERS:
            raise ServiceError(NOT_FOUND, "Connection not found", status=404, details={"id": identifier})
        before = effective_tls_trust(self._services.settings)
        self._services.gui_settings.set_value(TLS_SETTING, trust, actor=actor, channel=channel)    # 422 for anything else
        if before != trust:
            store = self._services.tasks.store
            with store.transaction() as conn:
                store.append_event(conn, TLS_EVENT, actor=actor, ref=identifier,
                                   cause=f"{identifier}: {before} -> {trust}",
                                   payload={"connection_id": identifier, "from": before, "to": trust})
        for adapter in getattr(self._services.system, "adapters", {}).values():
            reset = getattr(adapter, "reset_client", None)
            if callable(reset):
                reset()
        self._probed.clear()
        return self.probe(identifier)
