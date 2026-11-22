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

if TYPE_CHECKING:
    from . import ApplicationServices

GUI_SETTINGS_FILE = "gui-settings.json"
GUI_SETTINGS_SCHEMA = "maf.gui-settings/1"
MODES = ("simple", "advanced")
DEFAULT_MODE = "simple"
# The closed list: a new setting is one more entry here (ARR3-T07 adds provider_tls_trust, INI-T01 adds models).
ALLOWED_VALUES: dict[str, tuple[str, ...]] = {"mode": MODES}
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
    return kept


def _invalid(field_name: str, message: str) -> ServiceError:
    return ServiceError(INVALID_REQUEST, message, status=422, details={"field": field_name})


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
