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
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

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
    if isinstance(raw.get("model_levels"), dict):
        kept["model_levels"] = {model: value for model, value in raw["model_levels"].items()
                                if isinstance(model, str) and isinstance(value, dict)
                                and value.get("level") in ("T1", "T2", "T3", "T4")
                                and all(isinstance(value.get(key), str) for key in ("source", "confirmed_at", "confirmed_by"))}
    return kept


def _invalid(field_name: str, message: str) -> ServiceError:
    return ServiceError(INVALID_REQUEST, message, status=422, details={"field": field_name})


def _positive(value: Any) -> bool:
    return type(value) is int and 1 <= value <= 256


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

    def concurrency(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        saved = self.read().get("concurrency", {})
        config = _validate_concurrency(saved)
        mode = active_mode(config, now=now)
        return {**config, "mode": mode, "margin": config["margins"][mode],
                "reason": "temporary_mode_expired" if mode != config["mode"] else "saved_setting"}

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
