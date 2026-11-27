# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0
"""GUI settings that live on the server: ``<local_dir>/gui-settings.json`` (SIM-T01).

The simple or advanced mode belongs to the installation, not to a browser, so it is kept here.
The file holds a closed list of keys (``ALLOWED_VALUES``); a key or a value outside it is a 422.
A missing or broken file reads as the defaults and is not rewritten until the first change.
The server enforces only ``require_advanced_mode``; every other route is the same in both modes.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal

from ..config import DEFAULT_CONTINUITY_CANDIDATES
from .errors import INVALID_REQUEST, INVALID_STATE, ServiceError
from .concurrency import DEFAULT_MARGINS, MARGINS, MODES as CONCURRENCY_MODES, _instant, active_mode

if TYPE_CHECKING:
    from . import ApplicationServices

GUI_SETTINGS_FILE = "gui-settings.json"
GUI_SETTINGS_SCHEMA = "maf.gui-settings/1"
MODES = ("simple", "advanced")
DEFAULT_MODE = "simple"
# The closed list: a new setting is one more entry here (ARR3-T07 adds provider_tls_trust, INI-T01 adds models).
ALLOWED_VALUES: dict[str, tuple[str, ...]] = {"mode": MODES, "provider_tls_trust": ("certifi", "system"),
                                            "models": ("mock", "real")}
_DEFAULTS = {"mode": DEFAULT_MODE}
_META_KEYS = ("schema", "updated_at", "updated_by")
_PROVIDER = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_CONTINUITY_APPS = frozenset(("claude_cli", "codex_cli", "copilot_cli"))
# EXA-T05: the person's answer to "Search for apps on this computer". It is a key of its own, not one of ``ALLOWED_VALUES``,
# so the generic ``set_value`` can never write it; only ``set_app_discovery`` does, and only for the GUI person.
DISCOVERY_ANSWERS = ("accepted", "declined")


def load_file(local_dir: Path) -> dict[str, Any]:
    """The content of the file, or ``{}`` when it is missing or broken. For callers without the services."""
    try:
        data = json.loads((Path(local_dir) / GUI_SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _written(local_dir: Path) -> dict[str, Any]:
    """The valid keys of the file: anything outside the closed list or with a bad value reads as not written."""
    raw = load_file(local_dir)
    kept: dict[str, Any] = {key: raw[key] for key, allowed in ALLOWED_VALUES.items()
                            if isinstance(raw.get(key), str) and raw[key] in allowed}
    for key in ("updated_at", "updated_by"):
        if isinstance(raw.get(key), str):
            kept[key] = raw[key]
    if isinstance(raw.get("concurrency"), dict):
        try:
            kept["concurrency"] = _validate_concurrency(raw["concurrency"])
        except ServiceError:
            pass
    for key, validator in (("budget", _validate_budget), ("continuity", _validate_continuity)):
        if isinstance(raw.get(key), dict):
            try:
                kept[key] = validator(raw[key])
            except ServiceError:
                pass
    if isinstance(raw.get("model_levels"), dict):
        kept["model_levels"] = {model: value for model, value in raw["model_levels"].items()
                                if isinstance(model, str) and isinstance(value, dict)
                                and value.get("level") in ("T1", "T2", "T3", "T4")
                                and all(isinstance(value.get(key), str) for key in ("source", "confirmed_at", "confirmed_by"))}
    discovery = raw.get("app_discovery")
    if (isinstance(discovery, dict) and discovery.get("answer") in DISCOVERY_ANSWERS
            and all(isinstance(discovery.get(key), str) for key in ("at", "by"))):
        kept["app_discovery"] = {key: discovery[key] for key in ("answer", "at", "by")}
    if isinstance(raw.get("acp_consents"), dict):
        kept["acp_consents"] = {scope: {"allow_write": entry["allow_write"],
                                        **({"keep_history": entry["keep_history"]}
                                           if type(entry.get("keep_history")) is bool else {})}
                                for scope, entry in raw["acp_consents"].items()
                                if isinstance(scope, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}/[a-z0-9][a-z0-9-]{0,39}", scope)
                                and isinstance(entry, dict) and type(entry.get("allow_write")) is bool}
    return kept


def _invalid(field_name: str, message: str) -> ServiceError:
    return ServiceError(INVALID_REQUEST, message, status=422, details={"field": field_name})


def _positive(value: Any) -> bool:
    return type(value) is int and 1 <= value <= 256


def _validate_budget(value: Any, known: frozenset[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"providers"} or not isinstance(value["providers"], dict):
        raise _invalid("budget", "Invalid budget settings")
    providers = value["providers"]
    if len(providers) > 32:
        raise _invalid("providers", "Too many providers")
    for name, entry in providers.items():
        if not isinstance(name, str) or not _PROVIDER.fullmatch(name) or not isinstance(entry, dict) or set(entry) != {"amount", "mode"}:
            raise _invalid("providers", "Invalid provider budget")
        if known is not None and name not in known:
            raise _invalid("providers", f"Unknown provider: {name}")
        amount = entry["amount"]
        invalid_amount = amount is not None and (type(amount) not in (int, float) or amount < 0
                                                  or amount > 1_000_000_000
                                                  or (type(amount) is float and not isfinite(amount)))
        if invalid_amount or entry["mode"] not in ("warn", "cut"):
            raise _invalid("providers", "Invalid amount or budget mode")
    # -0.0 is stored as 0.0; ``0`` stays "spend nothing" and ``None`` stays "no budget".
    return {"providers": {name: {"amount": 0.0 if entry["amount"] == 0 and type(entry["amount"]) is float else entry["amount"],
                                 "mode": entry["mode"]} for name, entry in providers.items()}}


def _validate_continuity(value: Any) -> dict[str, Any]:
    base = {"mode", "wait_threshold_hours", "candidates"}
    if not isinstance(value, dict) or not base <= set(value) or set(value) - base - {"automatic", "automatic_since"}:
        raise _invalid("continuity", "Invalid continuity settings")
    if value["mode"] not in ("manual", "semi_auto"):
        raise _invalid("mode", "Unknown continuity mode")
    if type(value["wait_threshold_hours"]) is not int or value["wait_threshold_hours"] not in (1, 3, 5, 24):
        raise _invalid("wait_threshold_hours", "Invalid wait threshold")
    candidates = value["candidates"]
    if not isinstance(candidates, dict) or set(candidates) != _CONTINUITY_APPS or any(
            policy not in ("no_ask", "ask", "never") for policy in candidates.values()):
        raise _invalid("candidates", "Invalid continuity candidates")
    checked = {"mode": value["mode"], "wait_threshold_hours": value["wait_threshold_hours"],
               "candidates": dict(candidates)}
    # USO-T08: the switch «continue by itself». It is stored only while on, with the instant the server turned it on.
    automatic, since = value.get("automatic", False), value.get("automatic_since")
    if type(automatic) is not bool:
        raise _invalid("automatic", "Invalid automatic switch")
    if automatic:
        parsed = _instant(since) if isinstance(since, str) else None
        if parsed is None:
            raise _invalid("automatic_since", "The automatic switch needs the instant it was turned on")
        checked.update({"automatic": True, "automatic_since": parsed.strftime("%Y-%m-%dT%H:%M:%SZ")})
    elif since is not None:
        raise _invalid("automatic_since", "automatic_since belongs to the switch while it is on")
    return checked


def _validate_concurrency(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - {"mode", "margin", "margins", "manual", "per_app", "per_model", "allow_t4", "duration", "expires_at", "selection"}:
        raise _invalid("concurrency", "Invalid concurrency settings")
    mode = value.get("mode", "normal")
    if mode not in CONCURRENCY_MODES:
        raise _invalid("mode", "Unknown concurrency mode")
    margins = value.get("margins", DEFAULT_MARGINS)
    if not isinstance(margins, dict) or set(margins) != set(CONCURRENCY_MODES) or any(type(margins[key]) is not int or margins[key] not in MARGINS for key in CONCURRENCY_MODES):
        raise _invalid("margins", "Invalid margins")
    margin = value.get("margin", margins[mode])
    if type(margin) is not int or margin not in MARGINS:
        raise _invalid("margin", "Invalid margin")
    manual = value.get("manual", 256)
    if not _positive(manual):
        raise _invalid("manual", "Manual limit must be from 1 to 256")
    for key in ("per_app", "per_model"):
        limits = value.get(key, {})
        if not isinstance(limits, dict) or any(not isinstance(name, str) or not name or not _positive(limit)
                                               for name, limit in limits.items()):
            raise _invalid(key, "Invalid per-target limit")
    if type(value.get("allow_t4", False)) is not bool:
        raise _invalid("allow_t4", "allow_t4 must be boolean")
    if value.get("selection", "auto") not in ("auto", "manual"):
        raise _invalid("selection", "Invalid selection")
    duration = value.get("duration")
    if duration not in (None, "next_five_hour", "two_hours", "tasks"):
        raise _invalid("duration", "Invalid duration")
    if value.get("expires_at") is not None and _instant(value["expires_at"]) is None:
        raise _invalid("expires_at", "Invalid expiration")
    return {"mode": mode, "margin": margin, "margins": dict(margins), "manual": manual,
            "per_app": dict(value.get("per_app", {})), "per_model": dict(value.get("per_model", {})),
            "allow_t4": value.get("allow_t4", False), "duration": duration,
            "expires_at": value.get("expires_at"), "selection": value.get("selection", "auto")}


class GuiSettingsServices:
    """``services.gui_settings``: read and change the server-side GUI settings."""

    def __init__(self, services: ApplicationServices):
        self.services = services
        self._lock = threading.Lock()

    @property
    def _dir(self) -> Path:
        return Path(self.services.settings.local_dir)

    def read(self) -> dict[str, Any]:
        written = _written(self._dir)
        result: dict[str, Any] = {"schema": GUI_SETTINGS_SCHEMA, "mode": written.get("mode", DEFAULT_MODE)}
        result.update({key: value for key, value in written.items() if key not in result and key not in _META_KEYS})
        for key in ("updated_at", "updated_by"):
            if key in written:
                result[key] = written[key]
        return result

    def app_discovery(self) -> dict[str, Any] | None:
        """``{answer, at, by}`` of the search consent, or ``None`` while the person has not answered."""
        return self.read().get("app_discovery")

    def set_app_discovery(self, answer: str, *, actor: str = "user:gui", channel: str = "gui") -> dict[str, Any]:
        """Write the search consent (``accepted`` or ``declined``) with its own hour and actor. Only the GUI person."""
        if actor != "user:gui" or channel != "gui":
            raise _invalid("actor", "Only a GUI user may answer the app search question")
        if not isinstance(answer, str) or answer not in DISCOVERY_ANSWERS:
            raise _invalid("answer", f"answer must be one of: {', '.join(DISCOVERY_ANSWERS)}")
        with self._lock:
            content = {"schema": GUI_SETTINGS_SCHEMA, **_written(self._dir)}
            content["app_discovery"] = {"answer": answer, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                        "by": f"{actor}@{channel}"}
            self._write(content)
        return dict(content["app_discovery"])

    def acp_consent(self, scope: str) -> dict[str, Any] | None:
        """Return only an explicitly configured office/area policy."""
        return self.read().get("acp_consents", {}).get(scope)

    def set_acp_consent(self, scope: str, *, allow_write: bool,
                        keep_history: bool | None = None, actor: str = "user:gui") -> dict[str, Any]:
        if (actor != "user:gui" or not isinstance(scope, str) or not re.fullmatch(
                r"[a-z0-9][a-z0-9-]{0,39}/[a-z0-9][a-z0-9-]{0,39}", scope) or type(allow_write) is not bool
                or (keep_history is not None and type(keep_history) is not bool)):
            raise _invalid("acp_consent", "Invalid ACP consent")
        with self._lock:
            content = {"schema": GUI_SETTINGS_SCHEMA, **_written(self._dir)}
            previous = content.setdefault("acp_consents", {}).get(scope, {})
            entry = {"allow_write": allow_write}
            if keep_history is not None:
                entry["keep_history"] = keep_history
            elif "keep_history" in previous:
                entry["keep_history"] = previous["keep_history"]
            content["acp_consents"][scope] = entry
            content["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            content["updated_by"] = "user:gui@gui"
            self._write(content)
        return {"scope": scope, **entry}

    def concurrency(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        saved = self.read().get("concurrency", {})
        config = _validate_concurrency(saved)
        mode = active_mode(config, now=now)
        return {**config, "mode": mode, "margin": config["margins"][mode],
                "reason": "temporary_mode_expired" if mode != config["mode"] else "saved_setting"}

    def resolve(self, kind: Literal["budget", "continuity"]) -> dict[str, Any]:
        """The saved GUI value wins; otherwise the environment-backed settings remain in force."""
        saved = _written(self._dir).get(kind)
        settings = self.services.settings
        if kind == "budget":
            if saved is not None:
                budgets = [SimpleNamespace(id=f"gui.{provider}", scope="month", project=None, currency="USD",
                                           amount=entry["amount"], mode=entry["mode"], alerts=None, provider=provider)
                           for provider, entry in saved["providers"].items() if entry["amount"] is not None]
                return {"source": "gui", "providers": saved["providers"], "budgets": budgets,
                        "mode": settings.usage_budget_mode, "alerts": settings.usage_budget_alerts}
            return {"source": "environment" if "usage_budgets" in settings.model_fields_set else "default",
                    "providers": {}, "budgets": list(settings.usage_budgets),
                    "mode": settings.usage_budget_mode, "alerts": settings.usage_budget_alerts}
        if saved is not None:
            automatic = saved.get("automatic") is True
            return {"source": "gui", **saved, "max_handoffs": settings.continuity_max_handoffs,
                    "candidates": {**DEFAULT_CONTINUITY_CANDIDATES, **saved["candidates"]},
                    # USO-T08: with the switch on the mode in force is ``auto``; ``saved_mode`` comes back when it is off
                    "mode": "auto" if automatic else saved["mode"], "saved_mode": saved["mode"],
                    "automatic": automatic, "automatic_since": saved.get("automatic_since") if automatic else None}
        return {"source": "environment" if any(key in settings.model_fields_set for key in
                ("continuity_mode", "continuity_wait_threshold_hours", "continuity_max_handoffs",
                 "continuity_candidates")) else "default",
                "mode": settings.continuity_mode, "wait_threshold_hours": settings.continuity_wait_threshold_hours,
                "candidates": dict(settings.continuity_candidates), "max_handoffs": settings.continuity_max_handoffs,
                "saved_mode": settings.continuity_mode, "automatic": False, "automatic_since": None}

    def known_providers(self) -> frozenset[str] | None:
        """The providers of the catalog's models: the names ``BudgetGuard`` compares. ``None`` without a catalog."""
        models = getattr(getattr(self.services.system, "catalog", None), "models", None)
        if not models:
            return None
        return frozenset(spec.provider for spec in models.values() if getattr(spec, "provider", None))

    def set_usage(self, kind: Literal["budget", "continuity"], value: dict[str, Any], *,
                  actor: str = "user:gui") -> dict[str, Any]:
        if actor != "user:gui":
            raise _invalid("actor", "Only a GUI user may change usage settings")
        validator = _validate_budget if kind == "budget" else _validate_continuity
        checked = validator(value, self.known_providers()) if kind == "budget" else validator(value)
        with self._lock:
            content = {"schema": GUI_SETTINGS_SCHEMA, **_written(self._dir)}
            content[kind] = checked
            content["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            content["updated_by"] = "user:gui@gui"
            self._write(content)
        return self.resolve(kind)

    def set_concurrency(self, value: dict[str, Any], *, actor: str = "user:gui") -> dict[str, Any]:
        with self._lock:
            content = {"schema": GUI_SETTINGS_SCHEMA, **_written(self._dir)}
            previous = _validate_concurrency(content.get("concurrency", {}))
            now = datetime.now(timezone.utc)
            if active_mode(previous, now=now) != previous["mode"]:
                # A temporary mode that already ran out is Normal again before any change (partial or not) is merged.
                previous = {**previous, "mode": "normal", "margin": previous["margins"]["normal"],
                            "duration": None, "expires_at": None}
            if value.get("mode", "normal") not in CONCURRENCY_MODES:
                raise _invalid("mode", "Unknown concurrency mode")
            merged = {**previous, **value}
            if "margin" in value:
                merged["margins"] = {**previous["margins"], merged["mode"]: value["margin"]}
            elif "mode" in value:
                merged["margin"] = merged["margins"][merged["mode"]]
            if merged["mode"] in {"performance", "maximum"} and ("mode" in value or "duration" in value):
                from datetime import timedelta
                duration = merged.get("duration") or "next_five_hour"
                merged["duration"] = duration
                if duration == "two_hours":
                    merged["expires_at"] = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
                elif duration == "next_five_hour":
                    windows = [window for app in self.services.usage.plans().get("apps", [])
                               for window in app.get("windows", []) if window.get("window") == "five_hour"]
                    resets = [_instant(window.get("resets_at")) for window in windows]
                    future = [reset for reset in resets if reset is not None and reset > datetime.now(timezone.utc)]
                    merged["expires_at"] = min(future).isoformat(timespec="seconds") if future else (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
                else:
                    merged["expires_at"] = None
            elif merged["mode"] in {"eco", "normal"}:
                merged["duration"] = None
                merged["expires_at"] = None
            config = _validate_concurrency(merged)
            content["concurrency"] = config
            content["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            content["updated_by"] = f"{actor}@gui"
            self._write(content)
        return self.concurrency()

    def confirm_model_level(self, model_id: str, level: str, source: str, *, actor: str = "user:gui") -> dict[str, Any]:
        if not isinstance(model_id, str) or not model_id or len(model_id) > 200 or not isinstance(source, str) or not source.strip() or len(source) > 500 or level not in ("T1", "T2", "T3", "T4"):
            raise _invalid("model_level", "Invalid model classification")
        catalog = getattr(self.services.system, "catalog", None)
        if catalog is not None:
            try:
                catalog.model(model_id)
            except KeyError:
                raise _invalid("model_id", "Unknown model") from None
        record = {"level": level, "source": source, "confirmed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "confirmed_by": actor}
        with self._lock:
            content = {"schema": GUI_SETTINGS_SCHEMA, **_written(self._dir)}
            content.setdefault("model_levels", {})[model_id] = record
            self._write(content)
        tasks = getattr(self.services, "tasks", None)
        if tasks is not None:
            store = tasks.store
            with store.transaction() as conn:
                store.append_event(conn, "model.level_confirmed", actor=actor, ref=model_id,
                                   payload={"model_id": model_id, **record})
        return {"model_id": model_id, **record}

    def complete_temporary_tasks(self) -> dict[str, Any]:
        """Let the task runner end a temporary mode selected for this batch."""
        current = self.concurrency()
        if current["mode"] in {"performance", "maximum"} and current["duration"] == "tasks":
            return self.set_concurrency({"mode": "normal"})
        return current

    def mode(self) -> Literal["simple", "advanced"]:
        return self.read()["mode"]

    def value(self, key: str, default: str | None = None) -> str | None:
        return self.read().get(key, default) if key in ALLOWED_VALUES else default

    def set_mode(self, mode: str, *, actor: str = "user:gui", channel: str = "gui") -> dict[str, Any]:
        return self.set_value("mode", mode, actor=actor, channel=channel)

    def set_value(self, key: str, value: str, *, actor: str = "user:gui", channel: str = "gui") -> dict[str, Any]:
        if key not in ALLOWED_VALUES:
            raise _invalid("key", f"Unknown setting: {key}")
        if not isinstance(value, str) or value not in ALLOWED_VALUES[key]:
            raise _invalid(key, f"{key} must be one of: {', '.join(ALLOWED_VALUES[key])}")
        with self._lock:
            content = {"schema": GUI_SETTINGS_SCHEMA, **_written(self._dir)}
            content[key] = value
            content["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            content["updated_by"] = f"{actor}@{channel}"
            self._write(content)
        return self.read()

    def require_advanced_mode(self, action: str) -> None:
        """409 ``invalid_state`` with ``details.reason == "advanced_mode_required"`` while the mode is simple (L3)."""
        if self.mode() != "advanced":
            raise ServiceError(INVALID_STATE, "This action needs the advanced mode", status=409,
                               details={"reason": "advanced_mode_required", "action": action})

    def _write(self, content: dict[str, Any]) -> None:
        """Atomic: a temporary file in the same folder, then ``os.replace``. No ``.tmp`` is left behind."""
        folder = self._dir
        folder.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix=GUI_SETTINGS_FILE + ".", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(content, stream, indent=2, sort_keys=True)
                stream.write("\n")
            os.replace(name, folder / GUI_SETTINGS_FILE)
        except BaseException:
            try:
                os.unlink(name)
            except OSError:
                pass
            raise
